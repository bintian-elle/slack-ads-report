import json
import tempfile
import unittest
from pathlib import Path


class AuditTests(unittest.TestCase):
    def test_run_artifact_is_bounded_but_checkpoint_is_lossless(self):
        from kol_tracker import save_json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'kol_tracker_runtime'
            log=root/'runs'/'20261022T060001347762Z'/'row-3'/'plan.json'
            data={'planned':[{'row':3,'reason':'API unavailable','changes':{'7':100}}],
                  'requests':[{'secret':'x'*100000}],'native':{'sheets':[{'huge':'x'*100000}]}}
            save_json(log,data)
            self.assertLess(log.stat().st_size,4096)
            self.assertEqual(json.loads(log.read_text())['counts']['planned'],1)
            self.assertNotIn('requests',json.loads(log.read_text()))
            state=root/'row-progress-abc.json';save_json(state,data)
            self.assertEqual(json.loads(state.read_text()),data)

    def test_cleanup_removes_only_known_snapshots_in_timestamp_run_dirs(self):
        from kol_run_audit import clean_backups
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'runtime';run=root/'runs'/'20261022T060001347762Z'/'row-3'
            run.mkdir(parents=True)
            snapshot=run/'Organic-Meta-Totals-before.json';snapshot.write_text('backup')
            log=run/'plan.json';log.write_text('log')
            checkpoint=root/'meta-request-progress.json';checkpoint.write_text('checkpoint')
            unknown=run/'custom-before.json';unknown.write_text('manual')
            other=root/'runs'/'manual';other.mkdir();(other/'before.json').write_text('manual')
            preview=clean_backups(root,apply=False)
            self.assertEqual(preview['files'],1);self.assertTrue(snapshot.exists())
            result=clean_backups(root,apply=True)
            self.assertEqual(result['bytes'],6);self.assertFalse(snapshot.exists())
            for item in (log,checkpoint,unknown,other/'before.json'):self.assertTrue(item.exists())

    def test_cleanup_never_follows_symlink_to_other_data(self):
        from kol_run_audit import clean_backups
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'runtime';run=root/'runs'/'20261022T060001347762Z';run.mkdir(parents=True)
            outside=Path(directory)/'outside';outside.mkdir()
            keep=outside/'before.json';keep.write_text('important')
            (run/'linked').symlink_to(outside,target_is_directory=True)
            (run/'after.json').symlink_to(keep)
            self.assertEqual(clean_backups(root,apply=True)['files'],0)
            self.assertEqual(keep.read_text(),'important')

    def test_retention_prunes_only_old_timestamp_logs_not_state_or_manual_dirs(self):
        from kol_run_audit import prune_logs
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'runtime'
            for name in ('20260901T060000000000Z','20261022T060000000000Z','manual'):
                (root/'runs'/name).mkdir(parents=True)
                (root/'runs'/name/'plan.json').write_text('log')
            state=root/'live-metric-jobs.json';state.write_text('keep')
            prune_logs(root,now=datetime(2026,10,22,12,tzinfo=timezone.utc).timestamp())
            self.assertFalse((root/'runs'/'20260901T060000000000Z').exists())
            self.assertTrue((root/'runs'/'20261022T060000000000Z').exists())
            self.assertTrue((root/'runs'/'manual').exists());self.assertTrue(state.exists())
