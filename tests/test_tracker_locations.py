import unittest
from update_tracker_locations import location


class LocationTests(unittest.TestCase):
    def test_adset_group_takes_precedence(self):
        self.assertEqual(location({'adset':{'name':'26Jun_KOL1_Purchase'},'campaign':{'name':'SalesConversion'}}),'KOL1')

    def test_multiple_destination_classes(self):
        self.assertEqual(location({'adset':{'name':'FallSale_BidCap_140'}}),'BidCap')
        self.assertEqual(location({'adset':{'name':'PartnershipScale_Interest'}}),'Partnership-Scale')
        self.assertEqual(location({'adset':{'name':'Awareness_Videoview'}}),'Awareness')
        self.assertEqual(location({'adset':{'name':'Retargeting_Purchase'}}),'Retargeting')

    def test_unknown_destination_is_not_fabricated(self):
        self.assertEqual(location({'adset':{'name':'New_Test_Group'}}),'New_Test_Group')


if __name__=='__main__':unittest.main()
