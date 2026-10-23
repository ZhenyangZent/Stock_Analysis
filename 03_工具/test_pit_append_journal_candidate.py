from __future__ import annotations
import importlib.util, json, pathlib, tempfile, unittest

MODULE=pathlib.Path(__file__).with_name("pit_append_journal_candidate.py")
spec=importlib.util.spec_from_file_location("journal_transaction",MODULE)
jt=importlib.util.module_from_spec(spec); spec.loader.exec_module(jt)

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.tmp.name); self.e=jt.TransactionEngine(self.root)
        self.row={"event_id":"E1","created_at":"2025-06-01T00:00:00+00:00","value":1}
    def tearDown(self): self.tmp.cleanup()
    def prep(self,txid="TX1",**kw): return self.e.prepare("ai_reviews.jsonl",self.row,"2025-06-02T00:00:00+00:00",txid=txid,**kw)
    def crash(self,txid,point,**kw):
        with self.assertRaises(jt.InjectedCrash): self.e.commit(txid,crash_at=point,**kw)
    def recovered_once(self,txid):
        out=self.e.recover(txid); self.assertEqual("COMMITTED",out["state"])
        self.assertFalse(out["pit_certified"]); self.assertEqual(jt.UNVERIFIED,out["historical_prefix_provenance"])
        self.assertEqual(1,len(self.e.visible_rows("ai_reviews.jsonl")))
    def test_intent_written_ledger_not_written(self):
        tx=self.prep(); self.crash(tx,"after_intent"); self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_prepared_written_ledger_not_written(self):
        tx=self.prep(); self.crash(tx,"after_prepared"); self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_ledger_committed_journal_not_committed(self):
        tx=self.prep(); self.crash(tx,"after_ledger"); self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_journal_committed_ledger_not_committed(self):
        tx=self.prep(); self.crash(tx,"after_journal",order="journal_first"); self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_before_commit_marker_crash(self):
        tx=self.prep(); self.crash(tx,"before_marker"); self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_after_commit_marker_crash_is_committed(self):
        tx=self.prep(); self.crash(tx,"after_marker"); self.assertEqual(1,len(self.e.visible_rows("ai_reviews.jsonl"))); self.recovered_once(tx)
    def test_fsync_failure_then_recovery(self):
        tx=self.prep()
        with self.assertRaises(OSError): self.e.commit(tx,fail_at="fsync")
        self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_rename_failure_then_recovery(self):
        tx=self.prep()
        with self.assertRaises(OSError): self.e.commit(tx,fail_at="rename")
        self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_cas_failure_marks_recovery_required(self):
        tx=self.prep(); ledger,_,_=self.e.paths("ai_reviews.jsonl"); ledger.parent.mkdir(parents=True); ledger.write_bytes(b"foreign\n")
        with self.assertRaises(jt.TxError) as ctx: self.e.recover(tx)
        self.assertEqual("CAS_FAILED",ctx.exception.code); self.assertTrue((self.e.txroot/tx/"recovery_required.json").is_file())
    def test_audit_failure_then_recovery(self):
        tx=self.prep()
        with self.assertRaises(OSError): self.e.commit(tx,fail_at="audit")
        self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl")); self.recovered_once(tx)
    def test_multiple_writer_retry_same_transaction_is_idempotent(self):
        tx=self.prep(); self.assertEqual(tx,self.prep()); self.e.commit(tx); self.e.commit(tx); self.e.recover(tx)
        self.assertEqual(1,len(self.e.visible_rows("ai_reviews.jsonl"))); self.assertEqual(1,len(jt.rows(self.e.paths("ai_reviews.jsonl")[1].read_bytes())))
    def test_transaction_id_collision_rejected(self):
        self.prep(); other=dict(self.row,value=2)
        with self.assertRaises(jt.TxError) as ctx: self.e.prepare("ai_reviews.jsonl",other,"2025-06-02T00:00:00+00:00",txid="TX1")
        self.assertEqual("TRANSACTION_ID_COLLISION",ctx.exception.code)
    def test_raw_text_without_docs_ledger_invisible_then_recovered(self):
        tx=self.e.prepare("docs_ledger.jsonl",self.row,"2025-06-02T00:00:00+00:00",txid="DOC1",scope="docs",
            raw=("raw/docs/DOC1.htm",b"<p>official</p>"),text=("raw/docs/DOC1.txt",b"official\n"))
        self.crash(tx,"after_artifacts"); self.assertTrue((self.root/"raw/docs/DOC1.htm").is_file()); self.assertEqual([],self.e.visible_rows("docs_ledger.jsonl"))
        self.e.recover(tx); self.assertEqual(1,len(self.e.visible_rows("docs_ledger.jsonl")))
    def test_double_replay_does_not_duplicate(self):
        tx=self.prep(); self.e.commit(tx); a=self.e.recover(tx); b=self.e.recover(tx)
        self.assertEqual(a,b); self.assertEqual(1,len(self.e.visible_rows("ai_reviews.jsonl")))
    def test_uncommitted_transaction_not_visible(self):
        self.prep(); self.assertEqual([],self.e.visible_rows("ai_reviews.jsonl"))
    def test_abort_only_before_any_target_change(self):
        tx=self.prep(); out=self.e.abort(tx,"operator_cancelled"); self.assertEqual("ABORTED",out["state"])
    def test_abort_after_partial_commit_requires_recovery(self):
        tx=self.prep(); self.crash(tx,"after_ledger")
        with self.assertRaises(jt.TxError) as ctx: self.e.abort(tx,"bad")
        self.assertEqual("ABORT_REQUIRES_RECOVERY",ctx.exception.code)
    def test_journal_chain_tamper_detected(self):
        tx=self.prep(); self.e.commit(tx); _,journal,_=self.e.paths("ai_reviews.jsonl"); data=json.loads(journal.read_text()); data["chain_hash"]="0"*64; journal.write_bytes(jt.canonical(data))
        with self.assertRaises(jt.TxError): self.e.verify_committed(tx)
    def test_prefix_checkpoint_is_not_witness(self):
        tx=self.prep(); self.e.commit(tx); cp=jt.prefix_checkpoint(self.e.paths("ai_reviews.jsonl")[1].read_bytes(),1)
        self.assertEqual(jt.UNVERIFIED,cp["historical_prefix_provenance"]); self.assertFalse(cp["pit_certified"])
    def test_fixed_lock_order_docs_then_ai(self):
        self.assertEqual(("docs","ai"),self.e.LOCK_ORDER)
    def test_artifact_path_escape_rejected(self):
        with self.assertRaises(jt.TxError): self.e.prepare("docs_ledger.jsonl",self.row,"2025-06-02T00:00:00+00:00",raw=("../x",b"x"))

    def test_two_sequential_transactions_keep_both_prefix_certificates_valid(self):
        first=self.prep("TX1"); first_cert=self.e.commit(first)
        second_row=dict(self.row,event_id="E2",value=2)
        second=self.e.prepare("ai_reviews.jsonl",second_row,"2025-06-03T00:00:00+00:00",txid="TX2")
        second_cert=self.e.commit(second)
        self.assertEqual(first_cert,self.e.verify_committed(first))
        self.assertEqual(second_cert,self.e.verify_committed(second))
        self.assertEqual(["E1","E2"],[r["event_id"] for r in self.e.visible_rows("ai_reviews.jsonl")])

    def test_forged_marker_does_not_make_partial_transaction_visible(self):
        tx=self.prep(); self.crash(tx,"before_marker")
        td=self.e.txroot/tx
        forged={"schema_version":jt.SCHEMA,"transaction_id":tx,"state":"COMMITTED"}
        (td/"committed.json").write_bytes(jt.canonical(forged))
        with self.assertRaises(jt.TxError) as ctx: self.e.visible_rows("ai_reviews.jsonl")
        self.assertEqual("COMMIT_MARKER_IDENTITY_MISMATCH",ctx.exception.code)

    def test_visible_reader_rejects_journal_chain_tamper(self):
        tx=self.prep(); self.e.commit(tx); _,journal,_=self.e.paths("ai_reviews.jsonl")
        entry=json.loads(journal.read_text()); entry["previous_head"]="f"*64
        journal.write_bytes(jt.canonical(entry))
        with self.assertRaises(jt.TxError) as ctx: self.e.visible_rows("ai_reviews.jsonl")
        self.assertEqual("COMMITTED_JOURNAL_PREFIX_MISMATCH",ctx.exception.code)

    def test_visible_reader_rejects_missing_audit(self):
        tx=self.prep(); self.e.commit(tx); *_,audit=self.e.paths("ai_reviews.jsonl")
        audit.write_bytes(b"")
        with self.assertRaises(jt.TxError) as ctx: self.e.visible_rows("ai_reviews.jsonl")
        self.assertEqual("COMMITTED_AUDIT_PREFIX_MISMATCH",ctx.exception.code)

    def test_visible_reader_rejects_missing_artifact(self):
        tx=self.e.prepare("docs_ledger.jsonl",self.row,"2025-06-02T00:00:00+00:00",txid="DOC1",scope="docs",
            raw=("raw/docs/DOC1.htm",b"<p>official</p>"),text=("raw/docs/DOC1.txt",b"official\n"))
        self.e.commit(tx); (self.root/"raw/docs/DOC1.htm").unlink()
        with self.assertRaises(jt.TxError) as ctx: self.e.visible_rows("docs_ledger.jsonl")
        self.assertEqual("COMMITTED_ARTIFACT_MISMATCH",ctx.exception.code)

    def test_aborted_transaction_is_irreversible(self):
        tx=self.prep(); self.e.abort(tx,"operator_cancelled")
        for operation in (self.e.commit,self.e.recover):
            with self.assertRaises(jt.TxError) as ctx: operation(tx)
            self.assertEqual("TRANSACTION_ABORTED",ctx.exception.code)

    def test_committed_transaction_cannot_be_aborted(self):
        tx=self.prep(); self.e.commit(tx)
        with self.assertRaises(jt.TxError) as ctx: self.e.abort(tx,"late_cancel")
        self.assertEqual("COMMITTED_TRANSACTION_CANNOT_ABORT",ctx.exception.code)

    def test_transaction_identifier_path_safety(self):
        for txid in ("../TX","CON","bad:name","trailing.","a/b","a\\b"):
            with self.subTest(txid=txid), self.assertRaises(jt.TxError): self.prep(txid)

    def test_ledger_identifier_path_safety(self):
        for name in ("../ai_reviews.jsonl","CON.jsonl","a/b.jsonl","a\\b.jsonl","ai_reviews.txt"):
            with self.subTest(name=name), self.assertRaises(jt.TxError):
                self.e.prepare(name,self.row,"2025-06-02T00:00:00+00:00",txid="SAFE")

    def test_artifact_windows_and_alias_path_safety(self):
        bad=("raw/CON/file.htm","raw/docs/name. ","raw\\docs\\x.htm","C:/raw/x.htm")
        for relative in bad:
            with self.subTest(relative=relative), self.assertRaises(jt.TxError):
                self.e.prepare("docs_ledger.jsonl",self.row,"2025-06-02T00:00:00+00:00",txid="DOCSAFE",raw=(relative,b"x"))
        with self.assertRaises(jt.TxError) as ctx:
            self.e.prepare("docs_ledger.jsonl",self.row,"2025-06-02T00:00:00+00:00",txid="DOCALIAS",
                raw=("raw/docs/A.htm",b"a"),text=("raw/docs/a.HTM",b"b"))
        self.assertEqual("ARTIFACT_PATH_COLLISION",ctx.exception.code)

    def test_local_commit_never_claims_trusted_history(self):
        cert=self.e.commit(self.prep())
        self.assertFalse(cert["pit_certified"])
        self.assertEqual(jt.UNVERIFIED,cert["historical_prefix_provenance"])

if __name__=="__main__": unittest.main(verbosity=2)
