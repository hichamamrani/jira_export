import argparse
from dataclasses import dataclass
import plotly.express as px
import pandas as pd
from dateutil import parser

import requests
from datetime import datetime, timedelta

@dataclass
class StatusTimes:
    start: datetime
    end: datetime
    total_time: float

class JiraApiClient:
    def __init__(self, hostname, username, password):
        self.hostname = hostname
        self.username = username
        self.password = password

    def get_issues(self, project_key, start_date, end_date):
        api_url = f"https://{self.hostname}/rest/api/2/search"
        # JIRA Query to filter issues by project and date range
        jql = f" project={project_key} AND status in (Closed, Done) and type in (Story, Bug, Task) and resolutiondate >= '{start_date}' AND resolutiondate <= '{end_date}'"
        headers = {"Content-Type": "application/json"}

        response = requests.get(api_url, auth=(self.username, self.password), headers=headers, params={"jql": jql})

        if response.status_code == 200:
            return response.json()["issues"]
        else:
            print("Error:", response.status_code)
            return []

    def get_status_changes(self, issue_key):
        api_url = f"https://{self.hostname}/rest/api/2/issue/{issue_key}?expand=changelog"
        headers = {"Content-Type": "application/json"}

        response = requests.get(api_url, auth=(self.username, self.password), headers=headers)

        if response.status_code == 200:
            return response.json()["changelog"]["histories"]
        else:
            print("Error:", response.status_code)
            return []

    def get_releases_and_tickets(self, project_key, start_date, end_date):
        api_url = f"https://{self.hostname}/rest/api/2/project/{project_key}/versions"
        headers = {"Content-Type": "application/json"}

        response = requests.get(api_url, auth=(self.username, self.password), headers=headers)

        releases_and_tickets = []

        if response.status_code == 200:
            versions = response.json()
            for version in versions:
                release_date = version.get("releaseDate")
                if release_date and start_date <= release_date <= end_date:
                    release_name = version["name"]
                    release_tickets = self.get_tickets_in_release(project_key, version["id"])
                    release_duration = calculate_duration(version["startDate"], release_date)
                    releases_and_tickets.append({"Release": release_name, "Release Date": release_date, "Duration (days)": release_duration, "Tickets": release_tickets})
        else:
            print("Error:", response.status_code)

        return releases_and_tickets

    def get_tickets_in_release(self, project_key, release_id):
        api_url = f"https://{self.hostname}/rest/api/2/search"
        jql = f"project={project_key} AND fixVersion={release_id}"
        headers = {"Content-Type": "application/json"}

        response = requests.get(api_url, auth=(self.username, self.password), headers=headers, params={"jql": jql})

        if response.status_code == 200:
            return [issue["key"] for issue in response.json()["issues"]]
        else:
            print("Error:", response.status_code)
            return []



def parse_arguments():
    parser = argparse.ArgumentParser(description="Export JIRA issues to CSV.")
    parser.add_argument("project_key", type=str, help="JIRA project key")
    parser.add_argument("start_date", type=str, help="Start date (yyyy-mm-dd)")
    parser.add_argument("end_date", type=str, help="End date (yyyy-mm-dd)")
    parser.add_argument("filename", type=str, help="CSV name")
    parser.add_argument("--username", required=True, help="JIRA username")
    parser.add_argument("--password", required=True, help="JIRA API key or password")
    parser.add_argument("--hostname", required=True, help="JIRA hostname")
    return parser.parse_args()

def get_issue_type(issue):
    return issue["fields"]["issuetype"]["name"]

def is_weekend(date_str):
    # Convert date string to datetime object
    date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%f%z")
    # Check if the day is Saturday or Sunday (weekday() returns 0 for Monday, 6 for Sunday)
    return date.weekday() >= 5

def calculate_time_in_status(status_changes):
    status_times = {}
    prev_time = None

    for change in status_changes:
        created_time = datetime.strptime(change["created"], "%Y-%m-%dT%H:%M:%S.%f%z")
        time_in_status = 0

        for item in change["items"]:
            if item["field"] == "status":
                status = item["toString"]

                # print("To Status:", status)
                # print("From Status:", item["fromString"])
                # print("prev_time:", prev_time)
                # print("created_time:", created_time)

                if prev_time:
                    time_diff = prev_time - created_time
                    time_diff_days = time_diff.total_seconds() / (60 * 60 * 24)

                    current_time = prev_time
                    days_excluded = 0
                    while current_time < created_time:
                        if is_weekend(current_time.strftime("%Y-%m-%dT%H:%M:%S.%f%z")):
                            days_excluded += 2  # Count 2 days for the weekend
                        current_time += timedelta(days=1)

                    time_in_status += max(0, time_diff_days - days_excluded)
                    # print("time_diff_days:", time_diff_days)

                prev_time = created_time

                # print("Time in Status:", time_in_status)
                status_times[status] = status_times.get(status, 0) + time_in_status

    return status_times

def calculate_duration(start_date, end_date):
    start_datetime = parser.parse(start_date)
    end_datetime = parser.parse(end_date)
    duration = (end_datetime - start_datetime).days
    return duration

def merge_statuses(status):
    status_map = {
        "In QA": "QA",
        "Doing": "In progress",
        "In Progress / Development": "In progress",
        "In Progress/Development": "In progress",
        "Passed": "Signed Off/Ready for Release"
        # Add more status mappings if needed
    }
    return status_map.get(status, status)

@dataclass
class IssueData:
    issue: dict
    status_times: dict

exclude_statuses = {
    "Ready for Development",
    "Product Backlog",
    "Ready to Refine",
    "Triage",
    "Closed",
    "Matters Raised",
    "Done",
    "Backlog"
}

def main():
    args = parse_arguments()
    project_key = args.project_key
    start_date = args.start_date
    end_date = args.end_date
    username = args.username
    password = args.password
    hostname = args.hostname
    filename = args.filename

    jira_client = JiraApiClient(hostname, username, password)

    issues = jira_client.get_issues(project_key, start_date, end_date)
    releases_and_tickets = jira_client.get_releases_and_tickets(project_key, start_date, end_date)

    if not releases_and_tickets:
        print("No releases and tickets found.")
        return

    # Print releases and associated tickets
    for release_info in releases_and_tickets:
        print("Release:", release_info["Release"])
        print("Release Date:", release_info["Release Date"])
        print("Duration (days):", release_info["Duration (days)"])
        print("Tickets:", ", ".join(release_info["Tickets"]))
        print()

    if not issues:
        print("No issues found.")
        return

    # Create a list to store issue data
    issue_data_list = []

    for issue in issues:
        issue_key = issue["key"]
        status_changes = jira_client.get_status_changes(issue_key)

        status_times = calculate_time_in_status(status_changes)

        # Merge and exclude statuses
        modified_status_times = {}
        for status, time in status_times.items():
            merged_status = merge_statuses(status)
            if merged_status in exclude_statuses:
                continue
            if merged_status not in modified_status_times:
                modified_status_times[merged_status] = 0
            modified_status_times[merged_status] += time

        # Add issue data to the list
        issue_data = {
            "Issue Key": issue_key,
            "Issue Type": issue["fields"]["issuetype"]["name"],
            "Story Name": issue["fields"]["summary"]
        }
        issue_data.update(modified_status_times)  # Add merged status times to the issue data
        issue_data_list.append(issue_data)

    # Create a DataFrame from the issue data list
    df = pd.DataFrame(issue_data_list)
    dr = pd.DataFrame(releases_and_tickets)

    # Export the DataFrame to CSV
    csv_filename = f"jira_issues_{filename}.csv"
    df.to_csv(csv_filename, index=False)

    # Export the DataFrame to CSV
    csv_filename_release = f"jira_releases_{filename}.csv"
    dr.to_csv(csv_filename_release, index=False)

    # Generate an interactive pie chart using Pandas and Plotly
    status_totals = df.iloc[:, 3:].sum()  # Sum the status times using DataFrame functions
    fig = px.pie(values=status_totals, names=status_totals.index, title=f"Overall Status Distribution - {filename}")

    # Update the pie chart labels to include total hours spent for each category (status)
    fig.update_traces(
        textinfo='percent+label',
        texttemplate="%{percent:.4f}\n(%{value:.2f} hours)"
    )

    fig.show()

    # Generate a bar chart to visualize release durations
    release_duration_fig = px.bar(
        dr,
        x="Release",
        y="Duration (days)",
        title="Release Durations"
    )
    release_duration_fig.show()


if __name__ == "__main__":
    main()
