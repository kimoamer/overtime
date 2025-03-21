import frappe
from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip
from frappe.utils import (
	add_days,
	ceil,
	cint,
	cstr,
	date_diff,
	floor,
	flt,
	formatdate,
	get_first_day,
	get_link_to_form,
	getdate,
	money_in_words,
	rounded,
)
from frappe.query_builder.functions import Coalesce, Count, Round, Sum
from frappe.query_builder import Case, Order
from hris.utils.payroll_actions import get_start_end_dates
import datetime
from frappe import _

class SalarySlipNew(SalarySlip):
    def get_working_days_details(self, lwp=None, for_preview=0):
        payroll_settings = frappe.get_cached_value(
            "Payroll Settings",
            None,
            (
                "payroll_based_on",
                "include_holidays_in_total_working_days",
                "consider_marked_attendance_on_holidays",
                "daily_wages_fraction_for_half_day",
                "consider_unmarked_attendance_as",
            ),
            as_dict=1,
        )

        consider_marked_attendance_on_holidays = (
            payroll_settings.include_holidays_in_total_working_days
            and payroll_settings.consider_marked_attendance_on_holidays
        )

        daily_wages_fraction_for_half_day = flt(payroll_settings.daily_wages_fraction_for_half_day) or 0.5

        working_days = date_diff(self.end_date, self.start_date) + 1
        if for_preview:
            self.total_working_days = working_days
            self.payment_days = working_days
            return

        holidays = self.get_holidays_for_employee(self.start_date, self.end_date)
        working_days_list = [add_days(getdate(self.start_date), days=day) for day in range(0, working_days)]

        if not cint(payroll_settings.include_holidays_in_total_working_days):
            working_days_list = [i for i in working_days_list if i not in holidays]

            working_days -= len(holidays)
            if working_days < 0:
                frappe.throw(_("There are more holidays than working days this month."))

        if not payroll_settings.payroll_based_on:
            frappe.throw(_("Please set Payroll based on in Payroll settings"))

        if payroll_settings.payroll_based_on == "Attendance":
            actual_lwp, absent = self.calculate_lwp_ppl_and_absent_days_based_on_attendance(
                holidays, daily_wages_fraction_for_half_day, consider_marked_attendance_on_holidays
            )
            self.absent_days = absent
        else:
            actual_lwp = self.calculate_lwp_or_ppl_based_on_leave_application(
                holidays, working_days_list, daily_wages_fraction_for_half_day
            )

        if not lwp:
            lwp = actual_lwp
        elif lwp != actual_lwp:
            frappe.msgprint(
                _("Leave Without Pay does not match with approved {} records").format(
                    payroll_settings.payroll_based_on
                )
            )

        self.leave_without_pay = lwp
        self.total_working_days = working_days

        payment_days = self.get_payment_days(payroll_settings.include_holidays_in_total_working_days)

        if flt(payment_days) > flt(lwp):
            self.payment_days = flt(payment_days) - flt(lwp)

            if payroll_settings.payroll_based_on == "Attendance":
                self.payment_days -= flt(absent)

            consider_unmarked_attendance_as = payroll_settings.consider_unmarked_attendance_as or "Present"

            if (
                payroll_settings.payroll_based_on == "Attendance"
                and consider_unmarked_attendance_as == "Absent"
            ):
                unmarked_days = self.get_unmarked_days(
                    payroll_settings.include_holidays_in_total_working_days, holidays
                )
                self.absent_days += unmarked_days  # will be treated as absent
                self.payment_days -= unmarked_days
        else:
            self.payment_days = 0

    def calculate_lwp_ppl_and_absent_days_based_on_attendance(
        self, holidays, daily_wages_fraction_for_half_day, consider_marked_attendance_on_holidays
    ):
        lwp = 0
        absent = 0

        leave_type_map = self.get_leave_type_map()
        attendance_details = self.get_employee_attendance(
            start_date=self.start_date, end_date=self.actual_end_date
        )

        for d in attendance_details:
            if (
                d.status in ("Half Day", "On Leave")
                and d.leave_type
                and d.leave_type not in leave_type_map.keys()
            ):
                continue

            # skip counting absent on holidays
            if not consider_marked_attendance_on_holidays and getdate(d.attendance_date) in holidays:
                if d.status in ["Absent", "Half Day"] or (
                    d.leave_type
                    and d.leave_type in leave_type_map.keys()
                    and not leave_type_map[d.leave_type]["include_holiday"]
                ):
                    continue

            if d.leave_type:
                fraction_of_daily_salary_per_leave = leave_type_map[d.leave_type][
                    "fraction_of_daily_salary_per_leave"
                ]

            if d.status == "Half Day":
                equivalent_lwp = 1 - daily_wages_fraction_for_half_day

                if d.leave_type in leave_type_map.keys() and leave_type_map[d.leave_type]["is_ppl"]:
                    equivalent_lwp *= (
                        fraction_of_daily_salary_per_leave if fraction_of_daily_salary_per_leave else 1
                    )
                lwp += equivalent_lwp

            elif d.status == "On Leave" and d.leave_type and d.leave_type in leave_type_map.keys():
                equivalent_lwp = 1
                if leave_type_map[d.leave_type]["is_ppl"]:
                    equivalent_lwp *= (
                        fraction_of_daily_salary_per_leave if fraction_of_daily_salary_per_leave else 1
                    )
                lwp += equivalent_lwp

            elif d.status == "Absent":
                absent += 1

        return lwp, absent
