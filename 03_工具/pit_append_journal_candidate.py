"""External synthetic append-journal transaction and crash-recovery prototype.

This module is not wired to the Stock_Analysis repository. It issues no trusted
timestamp and does not certify historical rows. It models durable intent,
prepared candidates, deterministic recovery, and a commit/readback certificate.
"""
from __future__ import annotations
import hashlib, json, os, re, stat, tempfile, unicodedata, uuid
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "PIT_APPEND_TRANSACTION_V0.1_EXTERNAL_SYNTHETIC"
ZERO = "0" * 64
UNVERIFIED = "HISTORICAL_PREFIX_PROVENANCE_UNVERIFIED"
SAFE_ID=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
WINDOWS_RESERVED={"CON","PRN","AUX","NUL",*(f"COM{i}" for i in range(1,10)),*(f"LPT{i}" for i in range(1,10))}
ARTIFACT_NAMESPACE="raw"
REPARSE_POINT_ATTRIBUTE=getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",0x400)

class TxError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(code + (":" + detail if detail else ""))

class InjectedCrash(BaseException):
    pass

def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def rows(data: bytes) -> list[dict[str, Any]]:
    out=[]
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip(): continue
        item=json.loads(line)
        if not isinstance(item,dict): raise TxError("ROW_NOT_OBJECT",str(number))
        out.append(item)
    return out

def durable_write(path: Path, data: bytes, fail: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name=tempfile.mkstemp(prefix="."+path.name+".",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data)
            if fail=="fsync": raise OSError("INJECTED_FSYNC_FAILURE")
            f.flush(); os.fsync(f.fileno())
        if fail=="rename": raise OSError("INJECTED_RENAME_FAILURE")
        os.replace(name,path)
    finally:
        try: os.unlink(name)
        except OSError: pass

def read(path: Path) -> bytes:
    try: return path.read_bytes()
    except FileNotFoundError: return b""

def safe_id(value: str, label: str) -> str:
    if not isinstance(value,str) or not SAFE_ID.fullmatch(value) or value.endswith(("."," ")):
        raise TxError(label+"_INVALID",str(value))
    if value.split(".")[0].upper() in WINDOWS_RESERVED: raise TxError(label+"_INVALID",value)
    return value

def safe_ledger_name(value: str) -> str:
    safe_id(value,"LEDGER_NAME")
    if not value.endswith(".jsonl") or "/" in value or "\\" in value or ":" in value:
        raise TxError("LEDGER_NAME_INVALID",value)
    return value

def safe_artifact_path(value: str) -> str:
    if (not isinstance(value,str) or not value or "\\" in value or ":" in value or
            "//" in value or value.startswith("/") or value.endswith("/") or
            unicodedata.normalize("NFC",value)!=value):
        raise TxError("ARTIFACT_PATH_INVALID",str(value))
    lexical=value.split("/")
    p=PurePosixPath(value)
    if (p.is_absolute() or len(lexical)<2 or lexical[0]!=ARTIFACT_NAMESPACE or
            p.as_posix()!=value or tuple(lexical)!=p.parts or
            any(x in ("",".","..") or x.endswith(("."," ")) or
                x.split(".")[0].upper() in WINDOWS_RESERVED for x in lexical)):
        raise TxError("ARTIFACT_PATH_INVALID",value)
    return value

def is_reparse_point(path: Path) -> bool:
    try:
        info=os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info,"st_file_attributes",0)&REPARSE_POINT_ATTRIBUTE)

def path_identity(path: Path) -> tuple[int,int,int]:
    info=os.stat(path,follow_symlinks=False)
    return (int(info.st_dev),int(info.st_ino),int(getattr(info,"st_file_attributes",0)))

def chain_entry(sequence: int, transaction_id: str, ledger_name: str,
                row_hash: str, previous: str, appended_at: str) -> dict[str,Any]:
    body={"sequence":sequence,"transaction_id":transaction_id,"ledger_name":ledger_name,
          "row_sha256":row_hash,"previous_chain_hash":previous,"appended_at":appended_at}
    return dict(body,chain_hash=digest(canonical(body)))

def verify_chain(data: bytes) -> tuple[int,str]:
    previous=ZERO
    for number,item in enumerate(rows(data),1):
        if item.get("sequence")!=number or item.get("previous_chain_hash")!=previous:
            raise TxError("JOURNAL_CHAIN_ORDER_INVALID",str(number))
        body={k:item.get(k) for k in ("sequence","transaction_id","ledger_name","row_sha256","previous_chain_hash","appended_at")}
        if item.get("chain_hash")!=digest(canonical(body)):
            raise TxError("JOURNAL_CHAIN_HASH_INVALID",str(number))
        previous=item["chain_hash"]
    return len(rows(data)),previous

class TransactionEngine:
    LOCK_ORDER=("docs","ai")
    def __init__(self, root: Path):
        self.root=root; self.txroot=root/"transactions"; self.txroot.mkdir(parents=True,exist_ok=True)
        self.artifact_root=root/ARTIFACT_NAMESPACE
        self._artifact_pre_write_hook=lambda relative,target: None

    def _artifact_target(self, relative: str, *, create_parents: bool=False) -> Path:
        relative=safe_artifact_path(relative)
        if is_reparse_point(self.root): raise TxError("ARTIFACT_ROOT_REPARSE_POINT",str(self.root))
        if not self.root.is_dir(): raise TxError("ARTIFACT_ROOT_INVALID",str(self.root))
        current=self.root
        parts=relative.split("/")
        missing_parent=False
        for number,part in enumerate(parts):
            if missing_parent:
                current=current/part
                continue
            if is_reparse_point(current): raise TxError("ARTIFACT_REPARSE_POINT",str(current))
            if not current.is_dir(): raise TxError("ARTIFACT_PARENT_INVALID",str(current))
            aliases=[entry for entry in current.iterdir() if entry.name.casefold()==part.casefold()]
            if aliases and all(entry.name!=part for entry in aliases):
                raise TxError("ARTIFACT_CASEFOLD_COLLISION",relative)
            candidate=current/part
            final=number==len(parts)-1
            if candidate.exists() or is_reparse_point(candidate):
                if is_reparse_point(candidate): raise TxError("ARTIFACT_REPARSE_POINT",relative)
                if not final and not candidate.is_dir(): raise TxError("ARTIFACT_PARENT_INVALID",relative)
            elif create_parents and not final:
                candidate.mkdir()
                if is_reparse_point(candidate) or not candidate.is_dir():
                    raise TxError("ARTIFACT_PARENT_INVALID",relative)
            elif not candidate.exists():
                missing_parent=not final
            current=candidate
        return current

    def _directory_witness(self, target: Path) -> tuple[tuple[Path,tuple[int,int,int]],...]:
        chain=[]; current=self.root
        for part in target.relative_to(self.root).parts[:-1]:
            if is_reparse_point(current) or not current.is_dir():
                raise TxError("ARTIFACT_PARENT_INVALID",str(current))
            chain.append((current,path_identity(current))); current=current/part
        if is_reparse_point(current) or not current.is_dir():
            raise TxError("ARTIFACT_PARENT_INVALID",str(current))
        chain.append((current,path_identity(current)))
        return tuple(chain)

    def _verify_directory_witness(self, witness: tuple[tuple[Path,tuple[int,int,int]],...]) -> None:
        for path,identity in witness:
            if is_reparse_point(path) or not path.is_dir() or path_identity(path)!=identity:
                raise TxError("ARTIFACT_PARENT_IDENTITY_CHANGED",str(path))

    def _artifact_plan(self, td: Path, intent: dict[str,Any]) -> list[dict[str,Any]]:
        plans=[]; keys=set()
        for artifact in intent.get("artifacts",[]):
            if not isinstance(artifact,dict) or artifact.get("kind") not in ("raw","text"):
                raise TxError("ARTIFACT_METADATA_INVALID")
            relative=safe_artifact_path(artifact.get("path")); key=relative.casefold()
            if key in keys: raise TxError("ARTIFACT_PATH_COLLISION",relative)
            keys.add(key)
            source=td/(artifact["kind"]+".payload")
            if not source.is_file(): raise TxError("ARTIFACT_PAYLOAD_MISSING",relative)
            data=source.read_bytes()
            if len(data)!=artifact.get("byte_length") or digest(data)!=artifact.get("sha256"):
                raise TxError("ARTIFACT_PAYLOAD_IDENTITY_MISMATCH",relative)
            target=self._artifact_target(relative)
            if target.exists():
                current=target.read_bytes()
                if len(current)==len(data) and digest(current)==artifact["sha256"]:
                    action="ALREADY_APPLIED"
                else:
                    raise TxError("ARTIFACT_CAS_FAILED",relative)
            else:
                action="WRITE"
            plans.append({"relative":relative,"target":target,"data":data,"action":action})
        for plan in plans:
            target=self._artifact_target(plan["relative"],create_parents=True)
            if target!=plan["target"]: raise TxError("ARTIFACT_TARGET_CHANGED",plan["relative"])
            plan["witness"]=self._directory_witness(target)
        return plans

    def _apply_artifact_plan(self, plans: list[dict[str,Any]]) -> None:
        for plan in plans:
            self._verify_directory_witness(plan["witness"])
            target=self._artifact_target(plan["relative"])
            if plan["action"]=="ALREADY_APPLIED":
                if digest(target.read_bytes())!=digest(plan["data"]):
                    raise TxError("ARTIFACT_CAS_FAILED",plan["relative"])
                continue
            self._artifact_pre_write_hook(plan["relative"],target)
            self._verify_directory_witness(plan["witness"])
            if target.exists() or is_reparse_point(target):
                raise TxError("ARTIFACT_CAS_FAILED",plan["relative"])
            durable_write(target,plan["data"])
            self._verify_directory_witness(plan["witness"])
            checked=self._artifact_target(plan["relative"])
            if len(checked.read_bytes())!=len(plan["data"]) or digest(checked.read_bytes())!=digest(plan["data"]):
                raise TxError("ARTIFACT_READBACK_MISMATCH",plan["relative"])

    def paths(self, ledger_name: str) -> tuple[Path,Path,Path]:
        ledger_name=safe_ledger_name(ledger_name)
        return (self.root/"ledgers"/ledger_name,
                self.root/"journals"/(ledger_name+".journal.jsonl"),
                self.root/"audit"/(ledger_name+".audit.jsonl"))

    def prepare(self, ledger_name: str, row: dict[str,Any], appended_at: str,
                *, txid: str | None=None, scope: str="ai",
                raw: tuple[str,bytes] | None=None, text: tuple[str,bytes] | None=None) -> str:
        if scope not in self.LOCK_ORDER: raise TxError("LOCK_SCOPE_INVALID")
        txid=safe_id(txid or "TX_"+uuid.uuid4().hex,"TRANSACTION_ID")
        ledger_name=safe_ledger_name(ledger_name)
        td=self.txroot/txid
        if td.exists():
            intent=json.loads((td/"intent.json").read_bytes())
            requested_hash=digest(canonical(row))
            if (intent.get("transaction_id")!=txid or intent.get("ledger_name")!=ledger_name or
                intent.get("row_sha256")!=requested_hash):
                raise TxError("TRANSACTION_ID_COLLISION")
            return txid
        ledger,journal,audit=self.paths(ledger_name)
        before_l, before_j, before_a=read(ledger),read(journal),read(audit)
        row_bytes=canonical(row); seq,head=verify_chain(before_j)
        entry=chain_entry(seq+1,txid,ledger_name,digest(row_bytes),head,appended_at)
        entry_bytes=canonical(entry)
        audit_row={"transaction_id":txid,"ledger_name":ledger_name,"row_sha256":digest(row_bytes),"status":"COMMITTED"}
        artifacts=[]; artifact_keys=set()
        for kind,item in (("raw",raw),("text",text)):
            if item:
                relative,data=item
                relative=safe_artifact_path(relative); key=relative.casefold()
                if key in artifact_keys: raise TxError("ARTIFACT_PATH_COLLISION",relative)
                artifact_keys.add(key)
                artifacts.append({"kind":kind,"path":relative,"sha256":digest(data),"byte_length":len(data)})
        intent={"schema_version":SCHEMA,"transaction_id":txid,"state":"INTENT","scope":scope,
                "lock_order":list(self.LOCK_ORDER),"ledger_name":ledger_name,
                "row_sha256":digest(row_bytes),"row_bytes_hex":row_bytes.hex(),
                "ledger_before_sha256":digest(before_l),"ledger_before_length":len(before_l),
                "ledger_candidate_sha256":digest(before_l+row_bytes),"ledger_candidate_length":len(before_l+row_bytes),
                "journal_before_sha256":digest(before_j),"journal_before_length":len(before_j),
                "journal_candidate_sha256":digest(before_j+entry_bytes),"journal_candidate_length":len(before_j+entry_bytes),
                "journal_sequence":seq+1,"journal_previous_head":head,"journal_new_head":entry["chain_hash"],
                "journal_entry_hex":entry_bytes.hex(),"audit_before_sha256":digest(before_a),
                "audit_candidate_sha256":digest(before_a+canonical(audit_row)),
                "audit_candidate_length":len(before_a+canonical(audit_row)),
                "audit_row_hex":canonical(audit_row).hex(),"artifacts":artifacts}
        durable_write(td/"intent.json",canonical(intent))
        prepared=dict(intent,state="PREPARED",intent_sha256=digest(canonical(intent)))
        durable_write(td/"prepared.json",canonical(prepared))
        for kind,item in (("raw",raw),("text",text)):
            if item: durable_write(td/(kind+".payload"),item[1])
        return txid

    def _intent(self, txid: str) -> tuple[Path,dict[str,Any]]:
        txid=safe_id(txid,"TRANSACTION_ID")
        td=self.txroot/txid
        if not (td/"intent.json").is_file(): raise TxError("INTENT_MISSING",txid)
        return td,json.loads((td/"intent.json").read_bytes())

    def _apply_append(self,path:Path,before_hash:str,payload:bytes,candidate_hash:str,fail:str|None=None):
        current=read(path)
        if digest(current)==candidate_hash: return "ALREADY_APPLIED"
        if digest(current)!=before_hash: raise TxError("CAS_FAILED",str(path))
        durable_write(path,current+payload,fail)
        if digest(read(path))!=candidate_hash: raise TxError("READBACK_MISMATCH",str(path))
        return "APPLIED"

    def commit(self, txid: str, *, order: str="ledger_first", crash_at: str|None=None,
               fail_at: str|None=None) -> dict[str,Any]:
        td,intent=self._intent(txid)
        if (td/"aborted.json").is_file(): raise TxError("TRANSACTION_ABORTED",txid)
        if (td/"committed.json").is_file(): return self.verify_committed(txid)
        if crash_at=="after_intent": raise InjectedCrash(crash_at)
        if not (td/"prepared.json").is_file(): raise TxError("PREPARED_MISSING")
        if crash_at=="after_prepared": raise InjectedCrash(crash_at)
        ledger,journal,audit=self.paths(intent["ledger_name"])
        self._apply_artifact_plan(self._artifact_plan(td,intent))
        if crash_at=="after_artifacts": raise InjectedCrash(crash_at)
        row=bytes.fromhex(intent["row_bytes_hex"]); entry=bytes.fromhex(intent["journal_entry_hex"])
        ops=[("ledger",ledger,intent["ledger_before_sha256"],row,intent["ledger_candidate_sha256"]),
             ("journal",journal,intent["journal_before_sha256"],entry,intent["journal_candidate_sha256"])]
        if order=="journal_first": ops.reverse()
        for name,path,before,payload,candidate in ops:
            fail=fail_at if fail_at in ("fsync","rename") and name=="ledger" else None
            self._apply_append(path,before,payload,candidate,fail)
            if crash_at=="after_"+name: raise InjectedCrash(crash_at)
        audit_payload=bytes.fromhex(intent["audit_row_hex"])
        if fail_at=="audit": raise OSError("INJECTED_AUDIT_FAILURE")
        self._apply_append(audit,intent["audit_before_sha256"],audit_payload,intent["audit_candidate_sha256"])
        if crash_at=="before_marker": raise InjectedCrash(crash_at)
        cert={"schema_version":SCHEMA,"transaction_id":txid,"state":"COMMITTED",
              "ledger_prefix_sha256":intent["ledger_candidate_sha256"],
              "ledger_prefix_length":intent["ledger_candidate_length"],
              "journal_prefix_sha256":intent["journal_candidate_sha256"],
              "journal_prefix_length":intent["journal_candidate_length"],
              "journal_sequence":intent["journal_sequence"],"journal_head":intent["journal_new_head"],
              "audit_prefix_sha256":intent["audit_candidate_sha256"],
              "audit_prefix_length":intent["audit_candidate_length"],
              "intent_sha256":digest((td/"intent.json").read_bytes()),
              "historical_prefix_provenance":UNVERIFIED,"pit_certified":False}
        durable_write(td/"committed.json",canonical(cert))
        if crash_at=="after_marker": raise InjectedCrash(crash_at)
        return self.verify_committed(txid)

    def recover(self, txid: str) -> dict[str,Any]:
        td,intent=self._intent(txid)
        if (td/"aborted.json").is_file(): raise TxError("TRANSACTION_ABORTED",txid)
        if (td/"committed.json").is_file(): return self.verify_committed(txid)
        try:
            return self.commit(txid)
        except Exception as exc:
            marker={"transaction_id":txid,"state":"RECOVERY_REQUIRED","error":type(exc).__name__,"detail":str(exc)}
            durable_write(td/"recovery_required.json",canonical(marker))
            raise

    def abort(self,txid:str,reason:str)->dict[str,Any]:
        td,intent=self._intent(txid); ledger,journal,audit=self.paths(intent["ledger_name"])
        if (td/"committed.json").is_file(): raise TxError("COMMITTED_TRANSACTION_CANNOT_ABORT",txid)
        untouched=(digest(read(ledger))==intent["ledger_before_sha256"] and
                   digest(read(journal))==intent["journal_before_sha256"] and
                   digest(read(audit))==intent["audit_before_sha256"])
        if not untouched: raise TxError("ABORT_REQUIRES_RECOVERY")
        marker={"schema_version":SCHEMA,"transaction_id":txid,"state":"ABORTED","reason":reason}
        durable_write(td/"aborted.json",canonical(marker)); return marker

    def verify_committed(self,txid:str)->dict[str,Any]:
        td,intent=self._intent(txid); marker=td/"committed.json"
        if (td/"aborted.json").is_file(): raise TxError("TRANSACTION_ABORTED",txid)
        if not marker.is_file(): raise TxError("TRANSACTION_NOT_COMMITTED",txid)
        marker_bytes=marker.read_bytes(); cert=json.loads(marker_bytes)
        if marker_bytes!=canonical(cert): raise TxError("COMMIT_MARKER_NONCANONICAL")
        expected={"schema_version":SCHEMA,"transaction_id":txid,"state":"COMMITTED",
                  "ledger_prefix_sha256":intent["ledger_candidate_sha256"],
                  "ledger_prefix_length":intent["ledger_candidate_length"],
                  "journal_prefix_sha256":intent["journal_candidate_sha256"],
                  "journal_prefix_length":intent["journal_candidate_length"],
                  "journal_sequence":intent["journal_sequence"],"journal_head":intent["journal_new_head"],
                  "audit_prefix_sha256":intent["audit_candidate_sha256"],
                  "audit_prefix_length":intent["audit_candidate_length"],
                  "intent_sha256":digest((td/"intent.json").read_bytes()),
                  "historical_prefix_provenance":UNVERIFIED,"pit_certified":False}
        if cert!=expected: raise TxError("COMMIT_MARKER_IDENTITY_MISMATCH")
        ledger,journal,audit=self.paths(intent["ledger_name"])
        ledger_prefix=read(ledger)[:intent["ledger_candidate_length"]]
        journal_prefix=read(journal)[:intent["journal_candidate_length"]]
        audit_prefix=read(audit)[:intent["audit_candidate_length"]]
        if len(ledger_prefix)!=intent["ledger_candidate_length"] or digest(ledger_prefix)!=intent["ledger_candidate_sha256"]:
            raise TxError("COMMITTED_LEDGER_PREFIX_MISMATCH")
        if len(journal_prefix)!=intent["journal_candidate_length"] or digest(journal_prefix)!=intent["journal_candidate_sha256"]:
            raise TxError("COMMITTED_JOURNAL_PREFIX_MISMATCH")
        if len(audit_prefix)!=intent["audit_candidate_length"] or digest(audit_prefix)!=intent["audit_candidate_sha256"]:
            raise TxError("COMMITTED_AUDIT_PREFIX_MISMATCH")
        seq,head=verify_chain(journal_prefix)
        if head!=intent["journal_new_head"] or seq!=intent["journal_sequence"]: raise TxError("COMMITTED_HEAD_MISMATCH")
        ledger_rows=rows(ledger_prefix); journal_rows=rows(journal_prefix); audit_rows=rows(audit_prefix)
        if not ledger_rows or digest(canonical(ledger_rows[-1]))!=intent["row_sha256"]:
            raise TxError("COMMITTED_LEDGER_ROW_MISMATCH")
        entry=journal_rows[-1]
        if (entry.get("transaction_id")!=txid or entry.get("ledger_name")!=intent["ledger_name"] or
                entry.get("row_sha256")!=intent["row_sha256"]):
            raise TxError("COMMITTED_JOURNAL_ENTRY_MISMATCH")
        audit_row=audit_rows[-1]
        if (audit_row.get("transaction_id")!=txid or audit_row.get("ledger_name")!=intent["ledger_name"] or
                audit_row.get("row_sha256")!=intent["row_sha256"] or audit_row.get("status")!="COMMITTED"):
            raise TxError("COMMITTED_AUDIT_ROW_MISMATCH")
        for artifact in intent.get("artifacts",[]):
            relative=safe_artifact_path(artifact["path"]); target=self._artifact_target(relative)
            if not target.is_file(): raise TxError("COMMITTED_ARTIFACT_MISMATCH",relative)
            data=target.read_bytes()
            if len(data)!=artifact["byte_length"] or digest(data)!=artifact["sha256"]:
                raise TxError("COMMITTED_ARTIFACT_MISMATCH",relative)
        return cert

    def visible_rows(self,ledger_name:str)->list[dict[str,Any]]:
        ledger_name=safe_ledger_name(ledger_name)
        ledger,journal,_=self.paths(ledger_name); ledger_rows=rows(read(ledger)); journal_bytes=read(journal)
        verify_chain(journal_bytes); journal_rows=rows(journal_bytes)
        for marker in self.txroot.glob("*/committed.json"):
            txid=safe_id(marker.parent.name,"TRANSACTION_ID")
            _,intent=self._intent(txid)
            if intent.get("ledger_name")==ledger_name: self.verify_committed(txid)
        visible=[]
        for row,entry in zip(ledger_rows,journal_rows):
            txid=entry.get("transaction_id")
            if not isinstance(txid,str) or not (self.txroot/txid/"committed.json").is_file(): continue
            self.verify_committed(txid)
            if entry.get("ledger_name")!=ledger_name or entry.get("row_sha256")!=digest(canonical(row)):
                raise TxError("VISIBLE_ROW_BINDING_MISMATCH",str(txid))
            visible.append(row)
        return visible

    def recover_doc_artifacts(self,txid:str)->dict[str,Any]:
        td,intent=self._intent(txid)
        self._apply_artifact_plan(self._artifact_plan(td,intent))
        return self.recover(txid)

def prefix_checkpoint(journal_bytes:bytes,sequence:int)->dict[str,Any]:
    js=rows(journal_bytes)
    if not isinstance(sequence,int) or not 0<=sequence<=len(js): raise TxError("PREFIX_SEQUENCE_INVALID")
    return {"prefix_sequence":sequence,"prefix_head":js[sequence-1]["chain_hash"] if sequence else ZERO,
            "full_sequence":len(js),"full_head":js[-1]["chain_hash"] if js else ZERO,
            "historical_prefix_provenance":UNVERIFIED,"pit_certified":False}
