import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock
from google_sheets_service import GoogleSheetsService, EXPECTED_ACTUAL_HEADERS, validate_actual_pacing_headers, build_actual_total_spend_formula, extract_mtd_summary, MTD_SUMMARY_LABELS
from report_service import ChannelMetrics

class OctoberLayoutTests(unittest.TestCase):
    def rows(self, extended=True):
        header=['']*35
        for i,v in EXPECTED_ACTUAL_HEADERS.items(): header[i]=v
        header[30]='Meta ATC'
        if extended: header[30:35]=['ChatGPT Spend','ChatGPT ROAS','Meta ATC','google ads spend','google ads ROAS']
        return [['Actual Pacing'],header,['10/5/2026']]
    def test_both_layouts_and_unknown_rejected(self):
        self.assertFalse(validate_actual_pacing_headers(self.rows(False)))
        self.assertTrue(validate_actual_pacing_headers(self.rows()))
        rows=self.rows(); rows[1][34]='Other ROAS'
        with self.assertRaises(ValueError): validate_actual_pacing_headers(rows)
    def test_chatgpt_spend_in_total_without_changing_legacy(self):
        self.assertEqual(build_actual_total_spend_formula(73,True),'=M73+O73+Q73+S73+U73+W73+Y73+AA73+AC73+AE73')
        self.assertNotIn('AE',build_actual_total_spend_formula(73))
    def test_mtd_beyond_ai(self):
        rows=[]
        for label in MTD_SUMMARY_LABELS:
            rows.append(['']*35+[label,'123'])
        self.assertEqual(len(extract_mtd_summary(rows)),7)
    def test_write_targets_preserve_chatgpt_cells(self):
        g=object.__new__(GoogleSheetsService); g.base_url='https://example.invalid'
        g.list_tab_titles=Mock(return_value=['26 Oct - Budget Pacing'])
        g._read_tab_rows=Mock(return_value=self.rows())
        g.session=Mock()
        g._json_response=Mock(side_effect=lambda response, action: {} if action == 'batch write' else {'valueRanges': [{'values': item['values']} for item in g.session.post.call_args.kwargs['json']['data']]})
        g.write_actual_pacing(date(2026,10,5),[ChannelMetrics('Meta',Decimal('100'),Decimal('200'),add_to_cart=Decimal('4')),ChannelMetrics('Pmax',Decimal('50'),Decimal('100'))])
        data=g.session.post.call_args.kwargs['json']['data']
        targets={item['range'].split('!')[1]: item['values'][0][0] for item in data}
        self.assertEqual(targets['AG3'],4.0)
        self.assertEqual(targets['AH3'],50.0)
        self.assertEqual(targets['AI3'],2.0)
        self.assertNotIn('AE3',targets); self.assertNotIn('AF3',targets)
