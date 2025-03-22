import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields



def after_install():
	create_custom_fields(get_custom_fields())


def before_uninstall():
	delete_custom_fields(get_custom_fields())

def get_custom_fields():
    """ Return Custom Fields to masters of HRMS to fully Integerated """
    return {
		"Attendance":[
			{
				"fieldname": "time_section_break",
				"fieldtype": "Section Break",
				"label": "Time Details",
				"insert_after": "amended_from"
			},
			{
				"fieldname": "overtime",
				"fieldtype": "Float",
				"label": "Overtime",
				"insert_after": "time_section_break",
				"read_only": 1
			}
		],
		"Salary Slip": [
			{
				"fieldname": "overtime",
                "fieldtype": "Float",
                "label": "Overtime",
                "insert_after": "payment_days",
                "no_copy": 1,
                "read_only": 1
			},
			{
				"fieldname": "month_days",
                "fieldtype": "Int",
                "label": "Month Days",
                "insert_after": "leave_without_pay",
                "no_copy": 1,
                "read_only": 1
			},
			{
				"fieldname": "overtime_days",
                "fieldtype": "Float",
                "label": "Overtime Days",
                "insert_after": "overtime",
                "no_copy": 1,
                "read_only": 1
			},
		]
	}


        
def delete_custom_fields(custom_fields: dict):
	"""
	:Removing custom_fields: a dict like `{'Address': [{fieldname: 'eta_*', ...}]}`
	"""
	for doctype, fields in custom_fields.items():
		frappe.db.delete(
			"Custom Field",
			{
				"fieldname": ("in", [field["fieldname"] for field in fields]),
				"dt": doctype,
			},
		)

		frappe.clear_cache(doctype=doctype)