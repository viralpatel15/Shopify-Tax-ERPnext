app_name = "shopify_tax"
app_title = "Shopify Tax"
app_publisher = "Lyfe Hardware"
app_description = "Shopify tax integration for ERPNext"
app_email = "hello@lyfehardware.com"
app_license = "mit"

after_install = "shopify_tax.shopify_tax.setup.after_install"

doc_events = {
	"Sales Invoice": {
		"validate": "shopify_tax.shopify_tax.utils.set_sales_tax",
	},
	"Sales Order": {
		"validate": "shopify_tax.shopify_tax.utils.set_sales_tax",
	},
	"Quotation": {
		"validate": "shopify_tax.shopify_tax.utils.set_sales_tax",
	},
}
