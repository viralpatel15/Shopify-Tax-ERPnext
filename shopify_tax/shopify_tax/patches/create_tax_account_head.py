import frappe


def execute():
	"""Create Sales Tax Payable account for USD companies and populate Shopify Tax Settings."""
	companies = frappe.get_all("Company", filters={"default_currency": "USD"}, fields=["name"])

	for company_row in companies:
		company = company_row["name"]
		tax_account = _ensure_account(
			company=company,
			account_name="Sales Tax Payable",
			account_type="Tax",
			parent_keywords=["Duties and Taxes", "Tax Assets", "Current Liabilities"],
			root_type="Liability",
		)
		shipping_account = _ensure_account(
			company=company,
			account_name="Shipping Income",
			account_type="Income Account",
			parent_keywords=["Direct Income", "Sales", "Revenue"],
			root_type="Income",
		)
		_maybe_update_settings(company, tax_account, shipping_account)


def _ensure_account(company, account_name, account_type, parent_keywords, root_type):
	full_name = f"{account_name} - {frappe.db.get_value('Company', company, 'abbr')}"
	if frappe.db.exists("Account", full_name):
		return full_name

	parent = _find_parent_account(company, parent_keywords, root_type)
	if not parent:
		return None

	doc = frappe.get_doc({
		"doctype": "Account",
		"account_name": account_name,
		"parent_account": parent,
		"company": company,
		"account_type": account_type,
		"is_group": 0,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def _find_parent_account(company, keywords, root_type):
	for kw in keywords:
		result = frappe.db.get_value(
			"Account",
			{"company": company, "account_name": ["like", f"%{kw}%"], "is_group": 1, "root_type": root_type},
			"name",
		)
		if result:
			return result
	# fallback: first group account of root_type
	return frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": root_type},
		"name",
	)


def _maybe_update_settings(company, tax_account, shipping_account):
	if not frappe.db.exists("Shopify Tax Settings", "Shopify Tax Settings"):
		return
	settings = frappe.get_single("Shopify Tax Settings")
	changed = False
	if not settings.tax_account_head and tax_account:
		settings.tax_account_head = tax_account
		changed = True
	if not settings.shipping_account_head and shipping_account:
		settings.shipping_account_head = shipping_account
		changed = True
	if not settings.company:
		settings.company = company
		changed = True
	if changed:
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)
