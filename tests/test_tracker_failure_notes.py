import unittest
from tracker_failure_notes import plan_notes,HEADER


class FailureNotesTests(unittest.TestCase):
    def test_existing_column_english_reasons_and_clear(self):
        values=[['Creator','Metric',HEADER],[],['a',1,''],['b',2,'Update failed: old'],
                ['c',3,'Manual note'],['Summary']]
        notes=plan_notes(values,{4,5},[{'row':3,'reason':'No exact Ad ID match'}],0,3)
        self.assertEqual(notes,[{'row':3,'changes':{2:'Update failed: No exact Ad ID match'}},
                               {'row':4,'changes':{2:''}}])

    def test_meta_creator_column_and_partial_updates(self):
        values=[['Location','Creator',HEADER],[],['','a',''],['','b','']]
        notes=plan_notes(values,{3},[{'row':3,'reason':'Reach retained'},
                                     {'row':4,'reason':'No Insights'}],1,3)
        self.assertEqual(len(notes),2)
        self.assertIn('Reach retained',notes[0]['changes'][2])
        self.assertEqual(plan_notes([['Creator'],[],['a']],set(),[],0,3),[])
        with self.assertRaises(RuntimeError):
            plan_notes([[HEADER,HEADER]],set(),[],0,3)

