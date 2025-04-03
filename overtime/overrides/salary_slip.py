# Copyright (c) 2025, Innomate LLC. Ltd. and Contributors

import frappe
from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip
from frappe.utils import (
	add_days,
	cint,
	date_diff,
	flt,
	getdate,
)
from frappe.query_builder.functions import Count, Sum
from frappe.query_builder import Case
from frappe import _
from frappe.query_builder import DocType

from hrms.payroll.doctype.salary_slip.salary_slip_loan_utils import (
	process_loan_interest_accruals,
)

class SalarySlipNew(SalarySlip):
    def before_save(self):
        self.clac_month_days()
        self.leaves_count = self.get_leaves_count()

    def clac_month_days(self):
        first_day_of_month = frappe.utils.get_first_day(self.end_date)
        last_day_of_month = frappe.utils.get_last_day(self.end_date)
        diff_days = frappe.utils.date_diff(last_day_of_month,first_day_of_month)
        diff_days += 1
        self.month_days = diff_days
    
    @frappe.whitelist()
    def get_emp_and_working_day_details(self):
        """First time, load all the components from salary structure"""
        if self.employee:
            self.clac_month_days()
            self.leaves_count = self.get_leaves_count()
            self.set("earnings", [])
            self.set("deductions", [])
            if hasattr(self, "loans"):
                self.set("loans", [])

            if self.payroll_frequency:
                self.get_date_details()

            self.validate_dates()

            # getin leave details
            self.get_working_days_details()
            struct = self.check_sal_struct()

            if struct:
                self.set_salary_structure_doc()
                self.salary_slip_based_on_timesheet = (
                    self._salary_structure_doc.salary_slip_based_on_timesheet or 0
                )
                self.set_time_sheet()
                self.pull_sal_struct()

            process_loan_interest_accruals(self)
               
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
            actual_lwp, absent, overtime, overtime_days, days_of_overtime = self.calculate_lwp_ppl_and_absent_days_based_on_attendance(
                holidays, daily_wages_fraction_for_half_day, consider_marked_attendance_on_holidays
            )
            self.absent_days = absent
            self.overtime = overtime
            self.overtime_days = overtime_days
            self.days_of_overtime = days_of_overtime
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

    def get_employee_attendance_overtime(self, start_date, end_date):
        attendance = frappe.qb.DocType("Attendance")

        attendance_details = (
            frappe.qb.from_(attendance)
            .select(Sum(attendance.overtime).as_("overtime"), Sum(Case()
            .when(attendance.overtime >= 1, 1)
            .else_(0)).as_("days_of_overtime"))
            .where(
                (attendance.status.isin(["Present"]))
                & (attendance.employee == self.employee)
                & (attendance.docstatus == 1)
                & (attendance.attendance_date.between(start_date, end_date))
            )
        ).run(as_dict=1)

        return attendance_details

    def _get_marked_attendance_days_holidays(self, holidays: list | None = None) -> float:
        Attendance = frappe.qb.DocType("Attendance")
        query = (
            frappe.qb.from_(Attendance)
            .select(Count("*"))
            .where(
                (Attendance.attendance_date.between(self.actual_start_date, self.actual_end_date))
                & (Attendance.employee == self.employee)
                & (Attendance.docstatus == 1)
            )
        )
        if holidays:
            query = query.where(Attendance.attendance_date.isin(holidays))
        result = query.run()
        if len(result) > 0 :
            return result[0][0]
        else:
            return 0

    def calculate_lwp_ppl_and_absent_days_based_on_attendance(
        self, holidays, daily_wages_fraction_for_half_day, consider_marked_attendance_on_holidays
    ):
        lwp = 0
        absent = 0
        overtime = 0
        overtime_days = 0
        days_of_overtime = 0
        leave_type_map = self.get_leave_type_map()
        attendance_details = self.get_employee_attendance(
            start_date=self.start_date, end_date=self.actual_end_date
        )
        overtime_ = self.get_employee_attendance_overtime(start_date=self.start_date, end_date=self.actual_end_date)
        if len(overtime_) > 0 :
            overtime = overtime_[0].overtime
            days_of_overtime = overtime_[0].days_of_overtime
        overtime_days = self._get_marked_attendance_days_holidays(holidays)
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

        return lwp, absent, overtime, overtime_days, days_of_overtime

    def get_leaves_count(self):
        Attendance = DocType("Attendance")
        LeaveType = DocType("Leave Type")
        query = (
            frappe.qb.from_(Attendance)
            .join(LeaveType)
            .on(Attendance.leave_type == LeaveType.name)
            .select(Count(Attendance.name))
            .where((LeaveType.is_lwp == 0) & (Attendance.employee == self.employee))
            .where((Attendance.attendance_date >= self.start_date) & (Attendance.attendance_date <= self.end_date))
        )
        total_leaves = query.run()[0][0] if len(query.run()) > 0 else 0
        return total_leaves
