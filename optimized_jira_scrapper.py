import os
import time
import json
import requests
import concurrent.futures
from tqdm import tqdm
from pathlib import Path

# ================================================
# CONFIGURATION
# ================================================

BASE_URL = "https://issues.apache.org/jira/rest/api/2/search"
# Updated: Changed folder name from 'data/jsonl' to 'output'
OUTPUT_DIR = Path("output")
CHECKPOINT_DIR = Path("checkpoints")
MAX_RESULTS = 50  # Safe value for max results for each request
MAX_RETRIES = 5
RETRY_BACKOFF = 5
MAX_WORKERS = 5  # Safe number of concurrent requests

# Creates the 'output' folder and the 'checkpoints' folder
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

# NOTE: Environment variables are typically read at runtime in a local setup.
# In this environment, they are often unavailable, making AUTH likely None.
JIRA_USERNAME = os.getenv("JIRA_USERNAME")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
#required for private jira
AUTH = (JIRA_USERNAME, JIRA_API_TOKEN) if JIRA_USERNAME and JIRA_API_TOKEN else None


# ================================================
# SAFE REQUEST FUNCTION (with retries and backoff)
# ================================================

def safe_request(url, params=None):
    """
    Makes resilient HTTP GET requests with:
    - Retries for 429 and 5xx
    - Exponential backoff
    - Timeout and error handling
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, auth=AUTH, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"[429] Rate limit hit. Sleeping {retry_after}s...")
                time.sleep(retry_after)
            elif 500 <= resp.status_code < 600:
                wait = min(RETRY_BACKOFF * attempt, 60)
                print(f"[{resp.status_code}] Server error. Retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
            else:
                print(f"[{resp.status_code}] Unexpected response from server: {url}")
                # For 403 (Forbidden), 404 (Not Found), etc., stop and return None
                return None
        except requests.exceptions.RequestException as e:
            wait = min(RETRY_BACKOFF * attempt, 60)
            print(f"Network error: {e}. Retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
    print(f" Failed after {MAX_RETRIES} retries for URL={url}")
    return None


# ================================================
# CHECKPOINTS
# ================================================

def load_checkpoint(project_key):
    """Loads the last startAt position for a project from a checkpoint file."""
    f = CHECKPOINT_DIR / f"{project_key}_checkpoint.json"
    if f.exists():
        try:
            with open(f, "r") as fp:
                return json.load(fp).get("last_startAt", 0)
        except json.JSONDecodeError:
            print(f"Warning: Checkpoint file for {project_key} is corrupted. Starting from 0.")
            return 0
    return 0


def save_checkpoint(project_key, start_at):
    """Saves the current startAt position for a project."""
    f = CHECKPOINT_DIR / f"{project_key}_checkpoint.json"
    with open(f, "w") as fp:
        json.dump({"last_startAt": start_at}, fp)


# ================================================
# FETCH PAGINATED DATA
# ================================================

def get_total_issues(project_key):
    """
    Fetches the total number of issues for a given project.
    """
    # Request only 1 result to get the 'total' count efficiently
    params = {"jql": f"project={project_key}", "maxResults": 1}
    data = safe_request(BASE_URL, params)
    return data.get("total", 0) if data else 0


def fetch_page(project_key, start_at):
    """
    Fetches a single page of issues for a project.
    """
    params = {
        "jql": f"project={project_key}",
        "startAt": start_at,
        "maxResults": MAX_RESULTS,
        "expand": "comments"
    }
    data = safe_request(BASE_URL, params)
    if not data:
        # If request fails, return the current start_at and an empty list
        return start_at, []
    return start_at, data.get("issues", [])


# ================================================
# TRANSFORMATION LOGIC
# ================================================

def transform_issue(issue):
    """
    Convert a single JIRA issue into a structured JSONL record.
    Includes metadata, text, and derived tasks for LLM training/prompting.
    """
    fields = issue.get("fields", {})
    key = issue.get("key", "")
    project = fields.get("project", {}).get("key", "")
    summary = fields.get("summary", "")
    # Use empty string if description is None
    description = fields.get("description") or ""
    status = fields.get("status", {}).get("name", "")
    reporter = fields.get("reporter", {}).get("displayName", "")
    # Check if assignee exists before getting displayName
    assignee = fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned"
    priority = fields.get("priority", {}).get("name", "")
    labels = fields.get("labels", [])
    created = fields.get("created", "")
    updated = fields.get("updated", "")

    # Extract all comments as plain text
    comments_data = fields.get("comment", {}).get("comments", [])
    # Filter out comments with no body, and strip whitespace
    comments = [c.get("body", "").strip() for c in comments_data if c.get("body")]

    # Combine text for downstream NLP tasks
    full_text = f"{summary}\n\nDescription:\n{description}\n\nComments:\n" + "\n".join(comments)

    # Derived LLM tasks (examples of prompts for training data)
    derived_tasks = {
        "summarization": f"Summarize this issue: {full_text}",
        "classification": f"Classify the issue '{summary}' into categories like 'Bug', 'Improvement', 'Task', or 'Feature'.",
        "qna": f"Q: What is the main problem described in issue {key}?\nA: {description[:400]}"
    }

    return {
        "issue_key": key,
        "project": project,
        "title": summary,
        "status": status,
        "reporter": reporter,
        "assignee": assignee,
        "priority": priority,
        "labels": labels,
        "created": created,
        "updated": updated,
        "description": description.strip(),
        "comments": comments,
        "text": full_text.strip(),
        "derived_tasks": derived_tasks
    }


# ================================================
# SCRAPE ONE PROJECT (CONCURRENT)
# ================================================

def scrape_project(project_key):
    """
    Scrapes all issues for a single project concurrently using thread pooling.
    Saves output in both JSON and JSONL formats.
    """
    print(f"\n Scraper initiated for project: {project_key}")

    total = get_total_issues(project_key)

    # === MODIFICATION START: Print total issues ===
    print(f" Total issues found for {project_key}: {total}")
    # === MODIFICATION END ===

    if total == 0:
        print(f" No issues to scrape for {project_key}")
        return []

    # Calculate starting point and pages based on checkpoint
    start_checkpoint = load_checkpoint(project_key)
    pages = [start for start in range(start_checkpoint, total, MAX_RESULTS)]
    results = []

    print(f" Resuming from issue index {start_checkpoint}. Pages to fetch: {len(pages)}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Create a map of Future objects to their corresponding start_at index
        futures = {executor.submit(fetch_page, project_key, start): start for start in pages}

        with tqdm(total=len(pages), desc=f"{project_key} progress", unit="page") as pbar:
            for future in concurrent.futures.as_completed(futures):
                start_at = futures[future]
                try:
                    # The result is a tuple: (start_at, issues_list)
                    _, issues = future.result()
                    if issues:
                        for issue in issues:
                            results.append(transform_issue(issue))
                        # Update checkpoint only if the page fetch was successful
                        save_checkpoint(project_key, start_at + MAX_RESULTS)
                except Exception as e:
                    # This catches exceptions from fetch_page's execution
                    print(f"Error processing future for page {start_at}: {e}")

                pbar.update(1)

    # Save structured JSON array (.json)
    json_file = OUTPUT_DIR / f"{project_key.lower()}_issues.json"
    with open(json_file, "w", encoding="utf-8") as f:
        # Write the entire list of dictionaries as a single JSON array
        json.dump(results, f, indent=4, ensure_ascii=False)

    print(f" Finished {project_key}: {len(results)} structured records written to {json_file}")

    # Save structured JSON Lines (.jsonl)
    jsonl_file = OUTPUT_DIR / f"{project_key.lower()}_issues.jsonl"
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f" Finished {project_key}: {len(results)} structured records written to {jsonl_file}")

    return results


# ================================================
# MAIN ENTRYPOINT
# ================================================

def main():
    projects = ["ACCUMULO", "ACE", "AMQCPP"]
    for project in projects:
        scrape_project(project)


if __name__ == "__main__":
    main()
