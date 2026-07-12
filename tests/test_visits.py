"""VisitStore: page-view/unique-visitor counting for the admin portal (Day 38)."""

from __future__ import annotations

import unittest

from pbicompass.service.visits import VisitStore, visitor_hash


class VisitStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = VisitStore(":memory:")
        self.addCleanup(self.store.close)

    def test_records_and_counts_views(self):
        self.store.record("/", visitor_hash("s", "1.2.3.4", "ua-a"))
        self.store.record("/pricing", visitor_hash("s", "1.2.3.4", "ua-a"))
        self.store.record("/", visitor_hash("s", "5.6.7.8", "ua-b"))
        self.assertEqual(self.store.views_today(), 3)
        self.assertEqual(self.store.views_all_time(), 3)

    def test_unique_visitors_dedupes_same_ip_and_ua_same_day(self):
        h = visitor_hash("s", "1.2.3.4", "ua-a")
        self.store.record("/", h)
        self.store.record("/pricing", h)  # same visitor, different page
        self.store.record("/", visitor_hash("s", "5.6.7.8", "ua-b"))
        self.assertEqual(self.store.views_today(), 3)
        self.assertEqual(self.store.unique_visitors_today(), 2)
        self.assertEqual(self.store.unique_visitors_all_time(), 2)

    def test_visitor_hash_differs_by_ip_and_by_salt(self):
        a = visitor_hash("salt", "1.2.3.4", "ua")
        b = visitor_hash("salt", "9.9.9.9", "ua")
        c = visitor_hash("other-salt", "1.2.3.4", "ua")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_daily_breakdown_includes_zero_days_in_range(self):
        self.store.record("/", visitor_hash("s", "1.2.3.4", "ua"))
        days = self.store.daily_breakdown(7)
        self.assertEqual(len(days), 7)
        self.assertEqual(days[-1].views, 1)  # today is the last entry
        self.assertEqual(sum(d.views for d in days), 1)


if __name__ == "__main__":
    unittest.main()
