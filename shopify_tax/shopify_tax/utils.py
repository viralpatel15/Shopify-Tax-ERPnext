import json

import frappe
import requests
from frappe import _
from frappe.contacts.doctype.address.address import get_company_address
from frappe.utils import flt

SETTINGS_DOCTYPE = "Shopify Tax Settings"

SHOPIFY_API_VERSION = "2024-01"

SUPPORTED_STATE_CODES = [
	"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
	"GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
	"MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
	"NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
	"SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

DRAFT_ORDER_CALCULATE_MUTATION = """
mutation draftOrderCalculate($input: DraftOrderInput!) {
  draftOrderCalculate(input: $input) {
    calculatedDraftOrder {
      totalTax
      taxLines {
        rate
        title
        price
      }
      lineItems {
        taxLines {
          rate
          title
          price
        }
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""


def _log_request(url, request_data, response_body, *, doc=None, description=None, is_error=False):
	"""Write an outbound API call to Integration Request for audit / debugging."""
	try:
		log = {
			"doctype": "Integration Request",
			"integration_request_service": "Shopify Tax",
			"is_remote_request": 1,
			"url": url,
			"data": frappe.as_json(request_data, indent=1) if request_data is not None else None,
			"status": "Failed" if is_error else "Completed",
			"request_description": description or "Shopify Tax API Request",
		}
		if doc and not doc.is_new():
			log["reference_doctype"] = doc.doctype
			log["reference_docname"] = doc.name
		if is_error:
			log["error"] = frappe.as_json(response_body, indent=1) if response_body is not None else None
		else:
			log["output"] = frappe.as_json(response_body, indent=1) if response_body is not None else None

		frappe.get_doc(log).insert(ignore_permissions=True, ignore_links = True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Shopify Tax: failed to write Integration Request log")


DOCTYPE_FIELD_MAP = {
	"Sales Invoice": "enable_for_sales_invoice",
	"Sales Order": "enable_for_sales_order",
	"Quotation": "enable_for_quotation",
}


def set_sales_tax(doc, method):
	TAX_ACCOUNT_HEAD = frappe.db.get_single_value(SETTINGS_DOCTYPE, "tax_account_head")
	CALCULATE_TAX = frappe.db.get_single_value(SETTINGS_DOCTYPE, "calculate_tax")

	if not CALCULATE_TAX:
		return

	# Check per-doctype enable flag
	enable_field = DOCTYPE_FIELD_MAP.get(doc.doctype)
	if enable_field and not frappe.db.get_single_value(SETTINGS_DOCTYPE, enable_field):
		return

	if not _is_us_company(doc):
		return

	if not doc.items:
		return

	if check_sales_tax_exemption(doc):
		return

	to_address = get_shipping_address_details(doc)
	if not to_address:
		setattr(doc, "taxes", [tax for tax in doc.taxes if tax.account_head != TAX_ACCOUNT_HEAD])
		return

	to_country_code = frappe.db.get_value("Country", to_address.country, "code", cache=True)
	if not to_country_code or to_country_code.upper() != "US":
		setattr(doc, "taxes", [tax for tax in doc.taxes if tax.account_head != TAX_ACCOUNT_HEAD])
		return

	to_state = to_address.get("state", "")
	if to_state not in SUPPORTED_STATE_CODES:
		to_state = _get_state_code(to_address, "Shipping")

	# Build Shopify line items
	shopify_line_items = _build_line_items(doc)

	# Build Shopify shipping address
	shopify_address = _build_shopify_address(to_address)

	tax_result = _calculate_tax_via_shopify(shopify_line_items, shopify_address, doc=doc)
	if tax_result is None:
		return

	# tax_result.line_items is a list of {"tax_amount": float} matching doc.items order
	total_tax = flt(0)
	for idx, item in enumerate(doc.get("items")):
		item_taxable = flt(item.net_amount) if item.get("net_amount") else flt(item.get("amount", 0))
		item_tax = flt(tax_result.line_items[idx]["tax_amount"], 2) if idx < len(tax_result.line_items) else flt(0)
		item.tax_collectable = item_tax
		item.taxable_amount = item_taxable
		total_tax += item_tax

	total_tax = flt(total_tax, 2)

	if total_tax > 0:
		for tax in doc.taxes:
			if tax.account_head == TAX_ACCOUNT_HEAD:
				tax.tax_amount = total_tax
				break
		else:
			doc.append(
				"taxes",
				{
					"charge_type": "Actual",
					"description": "Sales Tax",
					"account_head": TAX_ACCOUNT_HEAD,
					"tax_amount": total_tax,
				},
			)
	else:
		setattr(doc, "taxes", [tax for tax in doc.taxes if tax.account_head != TAX_ACCOUNT_HEAD])

	doc.run_method("calculate_taxes_and_totals")


def _build_line_items(doc):
	"""Build Shopify custom line items from ERPNext items."""
	line_items = []
	for item in doc.get("items"):
		amount = flt(item.net_amount) if item.get("net_amount") else flt(item.get("amount", 0))
		qty = flt(item.qty) or 1
		unit_price = flt(amount / qty, 2) if qty else flt(amount, 2)
		line_items.append({
			"title": item.item_name or item.item_code,
			"originalUnitPrice": str(unit_price),
			"quantity": int(qty) if qty == int(qty) else 1,
			"taxable": True,
		})
	return line_items


def _build_shopify_address(address):
	"""Convert a Frappe Address doc to Shopify shippingAddress input."""
	state_code = address.get("state", "")
	if state_code not in SUPPORTED_STATE_CODES:
		try:
			state_code = _get_iso_3166_2_state_code(address)
		except Exception:
			pass

	return {
		"address1": address.get("address_line1") or "",
		"address2": address.get("address_line2") or "",
		"city": address.get("city") or "",
		"province": state_code,
		"zip": address.get("pincode") or "",
		"countryCode": "US",
	}


def _calculate_tax_via_shopify(line_items, shipping_address, doc=None):
	"""
	Call Shopify draftOrderCalculate to get tax for each line item.
	Returns frappe._dict with .line_items (list of {tax_amount}) or None on error.
	"""
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	store_url = settings.store_url
	access_token = settings.access_token and settings.get_password("access_token")

	if not store_url or not access_token:
		frappe.throw(_("Please configure Shopify store URL and access token in Shopify Tax Settings."))

	# Normalise store URL
	store_url = store_url.rstrip("/")
	if not store_url.startswith("http"):
		store_url = f"https://{store_url}"

	api_url = f"{store_url}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

	draft_input = {
		"lineItems": line_items,
		"shippingAddress": shipping_address,
	}

	payload = {
		"query": DRAFT_ORDER_CALCULATE_MUTATION,
		"variables": {"input": draft_input},
	}

	headers = {
		"X-Shopify-Access-Token": access_token,
		"Content-Type": "application/json",
	}

	try:
		response = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=15)
	except requests.ConnectionError:
		_log_request(api_url, payload, {"error": "ConnectionError"}, doc=doc, description="Tax Rate Lookup", is_error=True)
		frappe.throw(_("Could not reach Shopify. Please check your internet connection."))
	except requests.Timeout:
		_log_request(api_url, payload, {"error": "Timeout"}, doc=doc, description="Tax Rate Lookup", is_error=True)
		frappe.throw(_("Shopify tax request timed out. Please try again."))

	try:
		body = response.json()
	except Exception:
		body = {"raw": response.text[:500]}

	if response.status_code != 200:
		_log_request(api_url, payload, body, doc=doc, description="Tax Rate Lookup", is_error=True)
		frappe.log_error(
			title="Shopify Tax API error",
			message=f"URL: {api_url}\nHTTP {response.status_code}\nResponse: {body}",
		)
		frappe.throw(
			_("Shopify Tax API error (HTTP {0}). Check Error Log for details.").format(response.status_code)
		)

	# GraphQL-level errors (schema/field errors) — distinct from userErrors
	gql_errors = body.get("errors")
	if gql_errors:
		_log_request(api_url, payload, body, doc=doc, description="Tax Rate Lookup", is_error=True)
		frappe.log_error(
			title="Shopify Tax GraphQL error",
			message=f"URL: {api_url}\nErrors: {gql_errors}",
		)
		frappe.throw(_("Shopify Tax GraphQL error. Check Error Log for details."))

	errors = (body.get("data") or {}).get("draftOrderCalculate", {}).get("userErrors") or []
	if errors:
		_log_request(api_url, payload, body, doc=doc, description="Tax Rate Lookup", is_error=True)
		frappe.log_error(
			title="Shopify Tax userErrors",
			message=f"Errors: {errors}",
		)
		frappe.throw(_("Shopify Tax error: {0}").format(", ".join(e.get("message", "") for e in errors)))

	_log_request(api_url, payload, body, doc=doc, description="Tax Rate Lookup")

	calculated = (body.get("data") or {}).get("draftOrderCalculate", {}).get("calculatedDraftOrder") or {}
	total_tax = flt(calculated.get("totalTax", 0))

	# Use per-line-item tax from Shopify when available (more accurate, handles state-specific rules)
	shopify_line_items = calculated.get("lineItems") or []

	result_items = []
	if shopify_line_items:
		for node in shopify_line_items:
			item_tax = sum(flt(tl.get("price", 0)) for tl in (node.get("taxLines") or []))
			result_items.append({"tax_amount": flt(item_tax, 2)})

		# If per-item breakdown count doesn't match, fall back to proportional
		if len(result_items) != len(line_items):
			result_items = []

	if not result_items:
		# Fallback: distribute totalTax proportionally by line item amount
		amounts = [flt(li.get("originalUnitPrice", 0)) * int(li.get("quantity", 1)) for li in line_items]
		total_amount = sum(amounts)
		for amt in amounts:
			item_tax = flt(total_tax * amt / total_amount, 2) if total_amount else flt(0)
			result_items.append({"tax_amount": item_tax})

	return frappe._dict(line_items=result_items)



def check_sales_tax_exemption(doc):
	TAX_ACCOUNT_HEAD = frappe.db.get_single_value(SETTINGS_DOCTYPE, "tax_account_head")

	customer = getattr(doc, "customer", None) or getattr(doc, "party_name", None)

	doc_exempt = hasattr(doc, "exempt_from_sales_tax") and doc.exempt_from_sales_tax
	customer_exempt = (
		customer
		and frappe.db.has_column("Customer", "exempt_from_sales_tax")
		and frappe.db.get_value("Customer", customer, "exempt_from_sales_tax")
	)

	if doc_exempt or customer_exempt:
		for tax in doc.taxes:
			if tax.account_head == TAX_ACCOUNT_HEAD:
				tax.tax_amount = 0
				break
		doc.run_method("calculate_taxes_and_totals")
		return True

	return False


def get_company_address_details(doc):
	from erpnext import get_default_company

	doc_company_address = getattr(doc, "company_address", None)
	if doc_company_address:
		return frappe.get_doc("Address", doc_company_address)

	company = getattr(doc, "company", None) or get_default_company()
	company_address = get_company_address(company).company_address

	if not company_address:
		frappe.throw(_("Please set a default company address"))

	return frappe.get_doc("Address", company_address)


def get_shipping_address_details(doc):
	shipping_name = getattr(doc, "shipping_address_name", None)
	billing_name = getattr(doc, "customer_address", None)

	if shipping_name:
		return frappe.get_doc("Address", shipping_name)
	elif billing_name:
		return frappe.get_doc("Address", billing_name)
	else:
		return get_company_address_details(doc)


def _get_state_code(address, location):
	state_code = _get_iso_3166_2_state_code(address)
	if state_code not in SUPPORTED_STATE_CODES:
		frappe.throw(_("Please enter a valid State in the {0} Address").format(location))
	return state_code


def _get_iso_3166_2_state_code(address):
	import pycountry

	country_code = frappe.db.get_value("Country", address.get("country"), "code")
	state = address.get("state", "").upper().strip()

	error_message = _(
		"{0} is not a valid state! Check for typos or enter the ISO code for your state."
	).format(address.get("state"))

	if len(state) <= 3:
		address_state = (country_code + "-" + state).upper()
		states = [s.code for s in pycountry.subdivisions.get(country_code=country_code.upper())]
		if address_state in states:
			return state
		frappe.throw(_(error_message))
	else:
		try:
			lookup_state = pycountry.subdivisions.lookup(state)
		except LookupError:
			frappe.throw(_(error_message))
		else:
			return lookup_state.code.split("-")[1]


def _is_us_company(doc):
	company = getattr(doc, "company", None)
	if not company:
		from erpnext import get_default_company
		company = get_default_company()
	country = frappe.db.get_value("Company", company, "country")
	if not country:
		return False
	return country == "United States"
