"""Human labels remain pending until entered; offline scores bind to original inputs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from scripts.review_human_labels import export_dataset, rescore, validate_labels
from src.errors import ValidationError
from src.evaluation import load_dataset

PACKET = Path(__file__).resolve().parents[1] / 'evaluation/review_packets/human_review_12.v1.json'


class HumanReviewTests(unittest.TestCase):
    def setUp(self):
        self.packet=json.loads(PACKET.read_text())

    def complete_fixture(self):
        # Test-only labels and identity; never write them back to the review packet.
        self.packet.update(reviewer='unit-test-fixture',reviewed_at='2026-09-27T09:00:00+09:00')
        for case in self.packet['cases']:
            case.update(human_label='neutral',reason='Test-only placeholder for validation.')

    def test_pending_packet_cannot_be_exported(self):
        self.assertEqual(len(self.packet['cases']),12)
        self.assertTrue(all(c['human_label'] is None for c in self.packet['cases']))
        self.assertRaises(ValidationError,export_dataset,self.packet,'movie')
        self.complete_fixture(); self.packet['cases'][0]['human_label']=None
        self.assertRaises(ValidationError,export_dataset,self.packet,'product')

    def test_human_metadata_and_input_integrity_required(self):
        self.complete_fixture()
        original=copy.deepcopy(self.packet)
        for mutation in ('reviewer','reviewed_at','reason','text','duplicate'):
            self.packet=copy.deepcopy(original)
            if mutation in ('reviewer','reviewed_at'): self.packet[mutation]=''
            elif mutation=='reason': self.packet['cases'][0]['reason']=''
            elif mutation=='text': self.packet['cases'][0]['review_text']='changed'
            else: self.packet['cases'].append(copy.deepcopy(self.packet['cases'][0]))
            with self.subTest(mutation=mutation): self.assertRaises(ValidationError,validate_labels,self.packet)

    def test_export_is_domain_specific_and_compatible_with_evaluator(self):
        self.complete_fixture()
        self.packet['cases'][0]['human_label']='exclude'
        with tempfile.TemporaryDirectory() as tmp:
            for domain,count in [('product',5),('movie',6)]:
                result=export_dataset(self.packet,domain)
                path=Path(tmp)/f'{domain}.json';path.write_text(json.dumps(result))
                loaded=load_dataset(path)
                self.assertEqual(len(loaded['cases']),count)
                self.assertTrue(all(c['category']==domain for c in loaded['cases']))

    def test_saved_scores_are_recomputed_and_missing_or_changed_inputs_rejected(self):
        self.complete_fixture()
        cases=export_dataset(self.packet,'movie')['cases']
        rows=[dict(c,status='ok',predicted='positive') for c in cases]
        result=rescore(self.packet,{'rows':rows,'metrics':{'accuracy_all':1}},'movie')
        self.assertEqual(result['metrics']['accuracy_all'],0)
        self.assertEqual(result['api_calls'],0)
        rows[0]['predicted']='neutral'
        self.assertEqual(rescore(self.packet,{'rows':rows},'movie')['metrics']['correct'],1)
        self.assertRaises(ValidationError,rescore,self.packet,{'rows':rows[:-1]},'movie')
        altered=copy.deepcopy(rows);altered[0]['review_text']='changed'
        self.assertRaises(ValidationError,rescore,self.packet,{'rows':altered},'movie')
        altered=copy.deepcopy(rows);altered[0].update(status='error',predicted='positive')
        self.assertRaises(ValidationError,rescore,self.packet,{'rows':altered},'movie')
        rows[0].update(status='error',predicted=None)
        self.assertEqual(rescore(self.packet,{'rows':rows},'movie')['metrics']['errors'],1)


if __name__ == '__main__': unittest.main()
