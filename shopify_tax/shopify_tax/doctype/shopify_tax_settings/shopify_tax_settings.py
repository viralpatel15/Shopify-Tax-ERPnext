import frappe
from frappe import _
from frappe.model.document import Document

from shopify_tax.shopify_tax.setup import make_custom_fields

US_STATES = [
	("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
	("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
	("DC", "District of Columbia"), ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"),
	("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"),
	("KS", "Kansas"), ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"),
	("MD", "Maryland"), ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
	("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"),
	("NV", "Nevada"), ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"),
	("NY", "New York"), ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
	("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"),
	("SC", "South Carolina"), ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"),
	("UT", "Utah"), ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"),
	("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
]


class ShopifyTaxSettings(Document):
	def validate(self):
		if self.calculate_tax and not self.access_token:
			frappe.throw(_("Please enter an Admin API Access Token to enable tax calculation."))
		if self.calculate_tax and not self.store_url:
			frappe.throw(_("Please enter a Shopify Store URL to enable tax calculation."))

	def on_update(self):
		make_custom_fields()

	@frappe.whitelist()
	def add_all_nexus_states(self):
		"""Populate the nexus table with all 50 US states + DC."""
		existing_codes = {row.state_code for row in self.nexus}

		added = 0
		for code, name in US_STATES:
			if code not in existing_codes:
				self.append("nexus", {
					"state_code": code,
					"state": name,
					"country_code": "US",
				})
				added += 1

		self.save()
		frappe.msgprint(_("{0} state(s) added to nexus list.").format(added), alert=True)
