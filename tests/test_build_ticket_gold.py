import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import build_ticket_gold


class BuildTicketGoldTests(unittest.TestCase):
	@staticmethod
	def make_matches() -> pd.DataFrame:
		return pd.DataFrame({"match_id": [3, 1, 2]})

	@staticmethod
	def make_sales() -> pd.DataFrame:
		return pd.DataFrame(
			[
				{
					"ticket_sale_id": "SALE-1",
					"match_id": 1,
					"quantity": 2,
					"unit_price_nok": 300.0,
					"status": "completed",
				},
				{
					"ticket_sale_id": "SALE-2",
					"match_id": 1,
					"quantity": 1,
					"unit_price_nok": 250.0,
					"status": "completed",
				},
				{
					"ticket_sale_id": "SALE-3",
					"match_id": 1,
					"quantity": 5,
					"unit_price_nok": 300.0,
					"status": "cancelled",
				},
				{
					"ticket_sale_id": "SALE-4",
					"match_id": 2,
					"quantity": 1,
					"unit_price_nok": 400.0,
					"status": "refunded",
				},
			]
		)

	def build(self, matches=None, sales=None):
		return build_ticket_gold.build_match_ticket_sales(
			self.make_matches() if matches is None else matches,
			self.make_sales() if sales is None else sales,
		)

	def test_counts_only_completed_sales_and_preserves_every_match(self):
		frame = self.build().set_index("match_id")

		self.assertEqual(list(frame.index), [1, 2, 3])
		self.assertEqual(frame.loc[1, "completed_transactions"], 2)
		self.assertEqual(frame.loc[1, "tickets_sold"], 3)
		self.assertEqual(frame.loc[1, "gross_sales_nok"], 850.0)
		self.assertEqual(frame.loc[2, "tickets_sold"], 0)
		self.assertEqual(frame.loc[3, "gross_sales_nok"], 0.0)

	def test_rejects_unknown_match(self):
		sales = self.make_sales()
		sales.loc[0, "match_id"] = 99

		with self.assertRaisesRegex(build_ticket_gold.TicketGoldBuildError, "unknown match_id"):
			self.build(sales=sales)

	def test_rejects_duplicate_sale_identifier(self):
		sales = self.make_sales()
		sales.loc[1, "ticket_sale_id"] = "SALE-1"

		with self.assertRaisesRegex(build_ticket_gold.TicketGoldBuildError, "must be unique"):
			self.build(sales=sales)

	def test_rejects_invalid_quantity_and_price(self):
		for column, value in (("quantity", 0), ("quantity", 1.5), ("unit_price_nok", -1)):
			with self.subTest(column=column, value=value):
				sales = self.make_sales()
				sales[column] = sales[column].astype("object")
				sales.loc[0, column] = value
				with self.assertRaises(build_ticket_gold.TicketGoldBuildError):
					self.build(sales=sales)

	def test_output_is_deterministic_and_byte_identical(self):
		first = self.build()
		second = self.build()
		pd.testing.assert_frame_equal(first, second)

		with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
			first_path = build_ticket_gold.write_match_ticket_sales(
				first, Path(first_directory)
			)
			second_path = build_ticket_gold.write_match_ticket_sales(
				second, Path(second_directory)
			)
			self.assertEqual(first_path.read_bytes(), second_path.read_bytes())


if __name__ == "__main__":
	unittest.main()
