# Copyright (c) 2025, Innomate LLC. Ltd. and Contributors

from itertools import groupby
from hrms.hr.doctype.shift_type.shift_type import ShiftType
from frappe.utils import cint, create_batch
from hrms.hr.doctype.employee_checkin.employee_checkin import (
    calculate_working_hours,
    skip_attendance_in_checkins,
    update_attendance_in_checkins,
    handle_attendance_exception
)
from datetime import timedelta
import frappe
from frappe import _

EMPLOYEE_CHUNK_SIZE = 50

class DuplicateAttendanceError(frappe.ValidationError):
    pass


class OverlappingShiftAttendanceError(frappe.ValidationError):
    pass

class ShiftTypeNew(ShiftType):

    @frappe.whitelist()
    def process_auto_attendance(self):
        if (
            not cint(self.enable_auto_attendance)
            or not self.process_attendance_after
            or not self.last_sync_of_checkin
        ):
            return

        logs = self.get_employee_checkins()

        group_key = lambda x: (x["employee"], x["shift_start"])  # noqa
        for key, group in groupby(sorted(logs, key=group_key), key=group_key):
            single_shift_logs = list(group)
            attendance_date = key[1].date()
            employee = key[0]

            if not self.should_mark_attendance(employee, attendance_date):
                continue

            (
                attendance_status,
                working_hours,
                late_entry,
                early_exit,
                in_time,
                out_time,
                overtime
            ) = self.get_attendance(single_shift_logs)

            mark_attendance_and_link_log(
                single_shift_logs,
                attendance_status,
                attendance_date,
                working_hours,
                late_entry,
                early_exit,
                in_time,
                out_time,
                overtime,
                self.name,
            )

        # commit after processing checkin logs to avoid losing progress
        frappe.db.commit()  # nosemgrep

        assigned_employees = self.get_assigned_employees(self.process_attendance_after, True)
        # mark absent in batches & commit to avoid losing progress since this tries to process remaining attendance
        # right from "Process Attendance After" to "Last Sync of Checkin"
        for batch in create_batch(assigned_employees, EMPLOYEE_CHUNK_SIZE):
            for employee in batch:
                self.mark_absent_for_dates_with_no_attendance(employee)

            frappe.db.commit()  # nosemgrep


    def get_attendance(self, logs):
        """Return attendance_status, working_hours, late_entry, early_exit, in_time, out_time
        for a set of logs belonging to a single shift.
        Assumptions:
        1. These logs belongs to a single shift, single employee and it's not in a holiday date.
        2. Logs are in chronological order
        """
        late_entry = early_exit = False
        total_working_hours, in_time, out_time = calculate_working_hours(
            logs, self.determine_check_in_and_check_out, self.working_hours_calculation_based_on
        )
        if (
            cint(self.enable_late_entry_marking)
            and in_time
            and in_time > logs[0].shift_start + timedelta(minutes=cint(self.late_entry_grace_period))
        ):
            late_entry = True

        if (
            cint(self.enable_early_exit_marking)
            and out_time
            and out_time < logs[0].shift_end - timedelta(minutes=cint(self.early_exit_grace_period))
        ):
            early_exit = True
        overtime = 0
        if (
            out_time > logs[0].shift_end
        ):
            diff_min = round(float((out_time - logs[0].shift_end).total_seconds()) / 60, 2)
            overtime = round(diff_min / 60, 2)
        if (
            self.working_hours_threshold_for_absent
            and total_working_hours < self.working_hours_threshold_for_absent
        ):
            return "Absent", total_working_hours, late_entry, early_exit, in_time, out_time, overtime

        if (
            self.working_hours_threshold_for_half_day
            and total_working_hours < self.working_hours_threshold_for_half_day
        ):
            return "Half Day", total_working_hours, late_entry, early_exit, in_time, out_time, overtime

        return "Present", total_working_hours, late_entry, early_exit, in_time, out_time, overtime
    

def mark_attendance_and_link_log(
    logs,
    attendance_status,
    attendance_date,
    working_hours=None,
    late_entry=False,
    early_exit=False,
    in_time=None,
    out_time=None,
    overtime=None,
    shift=None,
):
    """Creates an attendance and links the attendance to the Employee Checkin.
    Note: If attendance is already present for the given date, the logs are marked as skipped and no exception is thrown.

    :param logs: The List of 'Employee Checkin'.
    :param attendance_status: Attendance status to be marked. One of: (Present, Absent, Half Day, Skip). Note: 'On Leave' is not supported by this function.
    :param attendance_date: Date of the attendance to be created.
    :param working_hours: (optional)Number of working hours for the given date.
    """
    log_names = [x.name for x in logs]
    employee = logs[0].employee

    if attendance_status == "Skip":
        skip_attendance_in_checkins(log_names)
        return None

    elif attendance_status in ("Present", "Absent", "Half Day"):
        try:
            frappe.db.savepoint("attendance_creation")
            attendance = frappe.new_doc("Attendance")
            attendance.update(
                {
                    "doctype": "Attendance",
                    "employee": employee,
                    "attendance_date": attendance_date,
                    "status": attendance_status,
                    "working_hours": working_hours,
                    "shift": shift,
                    "late_entry": late_entry,
                    "early_exit": early_exit,
                    "in_time": in_time,
                    "out_time": out_time,
                    "overtime": overtime,
                }
            ).submit()

            if attendance_status == "Absent":
                attendance.add_comment(
                    text=_("Employee was marked Absent for not meeting the working hours threshold.")
                )

            update_attendance_in_checkins(log_names, attendance.name)
            return attendance

        except frappe.ValidationError as e:
            handle_attendance_exception(log_names, e)

    else:
        frappe.throw(_("{} is an invalid Attendance Status.").format(attendance_status))
