import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from kol_live_insert import simple_organic_insert

class InsertTests(unittest.TestCase):
    def test_new_youtube_formulas_exclude_saves_shares_and_fee_stays_blank(self):
        header=['Creator','Organic Launch Date','Post Link','KOL Fee','Views','Interaction','Likes','Comments','Saves','Shares','CPM','CPE','Note','Reason for data update failure']
        values=[header,['old',46000,'https://youtu.be/ETVZaHw6gpc',999],['Summary','','',999]]
        rows=[{'values':[{} for _ in header]} for _ in values]
        rows[2]['values'][3]={'userEnteredValue':{'formulaValue':'=SUM(D2:D2)'}}
        native={'sheets':[{'data':[{'rowData':rows}]}]}
        item={'creator':'new','date':'2026-10-20','post_link':'https://youtu.be/m3voSsAfZQw','note':'Metrics pending immediate API update'}
        with patch('kol_tracker.save_json'):
            body=simple_organic_insert(Mock(),'endpoint',{'sheetId':1},native,values,'organic-youtube',[item],Path('/tmp'),False)
        cells=[x['updateCells'] for x in body if 'updateCells' in x]
        new={x['range']['startColumnIndex']:x['rows'][0]['values'][0] for x in cells if x['range']['startRowIndex']==2}
        self.assertNotIn(3,new)
        self.assertEqual(new[5]['userEnteredValue']['formulaValue'],'=G3+H3')
        self.assertIn('IFERROR',new[10]['userEnteredValue']['formulaValue'])
        self.assertIn('=SUM(D2:D3)',str(cells))
