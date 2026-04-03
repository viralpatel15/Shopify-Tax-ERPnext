import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
	make_custom_fields()
	from shopify_tax.shopify_tax.patches.create_tax_account_head import execute as create_tax_account_head
	create_tax_account_head()


def make_custom_fields(update=True):
	"""Add tax_collectable and taxable_amount to item child tables."""
	item_tax_fields = [
		dict(
			fieldname="tax_collectable",
			fieldtype="Currency",
			insert_after="net_amount",
			label="Tax Collectable",
			read_only=1,
			options="currency",
		),
		dict(
			fieldname="taxable_amount",
			fieldtype="Currency",
			insert_after="tax_collectable",
			label="Taxable Amount",
			read_only=1,
			options="currency",
		),
	]

	custom_fields = {
		"Sales Invoice Item": item_tax_fields,
		"Sales Order Item": item_tax_fields,
		"Quotation Item": item_tax_fields,
	}
	create_custom_fields(custom_fields, update=update)
