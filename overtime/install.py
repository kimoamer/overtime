import click

from overtime.setup import after_install as setup


def after_install():
	try:
		print("Setting up Overtime...")
		setup()

		click.secho("Thank you for installing Overtime!", fg="green")

	except Exception as e:
		BUG_REPORT_URL = "https://github.com/kimoamer/Error-Management/issues/new"
		click.secho(
			"Installation for Overtime app failed due to an error."
			" Please try re-installing the app or"
			f" report the issue on {BUG_REPORT_URL} if not resolved.",
			fg="bright_red",
		)
		raise e