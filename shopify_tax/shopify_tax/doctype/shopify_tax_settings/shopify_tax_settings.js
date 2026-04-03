frappe.ui.form.on("Shopify Tax Settings", {
	onload(frm) {
		["tax_account_head", "shipping_account_head"].forEach((field) => {
			frm.set_query(field, () => ({
				filters: {
					company: frm.doc.company || frappe.defaults.get_default("company"),
					is_group: 0,
				},
			}));
		});
	},

	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Add All US States to Nexus"), () => {
				frappe.confirm(
					__("This will add all 50 US states + DC to the nexus list. Continue?"),
					() => {
						frm.call("add_all_nexus_states").then(() => frm.refresh());
					}
				);
			});
		}
	},
});
