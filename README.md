# **JIRA Issue Scraper (Apache Public JIRA)**

This project builds a robust, fault-tolerant, and scalable data scraping pipeline to extract issue data from Apache’s public Jira instance and transform it into a clean JSONL dataset suitable for Large Language Model (LLM) training.

The system fetches issue metadata, descriptions, and comments, handles network edge cases, and converts unstructured issue data into structured training-ready corpora.

## **1\. Setup Instructions and Environment Configuration**

### **Prerequisites**

You need Python 3.8 or higher installed on your system.

### **Creating a Virtual Environment (Recommended)**

It is best practice to isolate project dependencies using a virtual environment.

1. **Create the environment (named .venv):**  
   python \-m venv .venv

2. **Activate the environment:**  
   * **Linux/macOS:**  
     source .venv/bin/activate

   * **Windows (Command Prompt):**  
     .venv\\Scripts\\activate.bat

   * **Windows (PowerShell):**  
     .venv\\Scripts\\Activate.ps1

### **Dependencies**

This script requires the following Python libraries: requests and tqdm.

Install all dependencies from the provided **requirements.txt** file:

**pip install -r requirements.txt**

### **Environment Configuration (Authentication)**

The JIRA API documentation specifies that authentication may not be required for public, read-only access (like the Apache JIRA used by default). However, if you are targeting a **private JIRA instance** or encounter rate limits, authentication is necessary.

1. **Obtain Credentials:** Get your JIRA username and a corresponding API token.  
2. **Set Environment Variables:** Set the following variables in your shell *before* running the script:  
   export JIRA\_USERNAME="your\_jira\_username"  
   export JIRA\_API\_TOKEN="your\_jira\_api\_token"

   The script will use these credentials automatically.

### **Running the Scraper**

1. Ensure the virtual environment is activated and the dependencies are installed.  
2. Run the main script:  
    python .\optimized_jira_scrapper.py

The scraped data will be saved into the newly created **output** directory, and checkpoints will be saved in the **checkpoints** directory.

## **2\. Architecture Overview and Design Reasoning**

| Component | Purpose | Design Rationale |
| :---- | :---- | :---- |
| safe\_request | Handles all external API calls. | **Resilience:** Centralizes error handling, retries, and backoff logic, protecting against transient network issues and API throttling. |
| get\_total\_issues | Fetches the total issue count. | **Efficiency:** Determines the total workload (total issues) and the number of pages needed upfront. |
| load\_checkpoint/save\_checkpoint | Manages project-specific progress. | **Fault Tolerance:** Allows the script to resume from the last successfully processed page if it is stopped or crashes. |
| fetch\_page | Retrieves a single page of issues. | **Modularity:** Isolates the pagination logic. Designed to be safely run concurrently. |
| transform\_issue | Cleans and structures a raw JIRA issue object. | **Data Integrity:** Ensures consistent data schema, combines text fields, and pre-generates common LLM task prompts (summarization, classification, Q\&A). |
| scrape\_project | Coordinates the concurrent fetching and saving. | **Performance:** Uses concurrent.futures.ThreadPoolExecutor to execute multiple fetch\_page requests simultaneously, dramatically speeding up the scraping process. |

## **3\. Detailed Explanation of Edge Cases Handled**

The safe\_request function is the core of the resilience strategy and explicitly handles several critical edge cases:

| Edge Case | Status Code | Handling Mechanism |
| :---- | :---- | :---- |
| **Rate Limiting** | 429 (Too Many Requests) | Reads the Retry-After header from the response (if available) and enforces a sleep period, respecting the API limits. |
| **Server Errors** | 500-599 (Internal Server Error, etc.) | Uses **exponential backoff** (RETRY\_BACKOFF \* attempt) for up to MAX\_RETRIES. This prevents flooding a potentially unstable server. |
| **Network Errors** | requests.exceptions.RequestException | Catches exceptions like timeouts, DNS failures, or connection resets, and retries with exponential backoff. |
| **Authentication/Access** | Non-200 codes (e.g., 403, 404\) | Prints an error message and terminates the request attempts, as retrying will not resolve permanent access issues. |
| **Checkpoint Corruption** | json.JSONDecodeError | The load\_checkpoint function includes a try/except block to detect a corrupted checkpoint file and gracefully defaults the start position back to 0\. |
| **Partial Fetch Failure** | During concurrent execution | The use of checkpoints inside the try block of the concurrent loop ensures that *only* successfully completed pages update the checkpoint. If a thread fails, the checkpoint remains at the previous successful position, guaranteeing the failed page is retried in a subsequent run. |

## **4\. Optimization Decisions and Potential Future Improvements**

### **Optimization Decisions Implemented**

1. **Concurrency (MAX\_WORKERS):** Using a ThreadPoolExecutor drastically reduces the total scraping time by making multiple API requests in parallel rather than waiting for each one sequentially.  
2. **Checkpointing:** Critical for long-running jobs. It prevents the need to restart the entire scrape for a project from issue 0\.  
3. **Optimized Total Count:** The get\_total\_issues function uses maxResults=1 to minimize payload size when determining the total issue count, which is necessary for calculating the pagination ranges.  
4. **Dual Output Format:** Saving to both **.json** (for human readability, easy loading into tools) and **.jsonl** (standard format for large-scale data processing and LLM training) maximizes data utility.

### **Potential Future Improvements**

1. **Project List Externalization:** The list of projects (\["ACCUMULO", "ACE", "AMQCPP"\]) could be moved to a configuration file (like a .yaml or .ini) or accepted as a command-line argument for greater flexibility.  
2. **Dynamically Adjusting MAX\_WORKERS:** Implement logic to dynamically reduce MAX\_WORKERS if persistent 429 rate limit errors are encountered, providing a more adaptive throttling mechanism.  
3. **Detailed Field Selection:** Currently, the code uses JIRA's default fields. The jql parameters could be updated to explicitly request only the necessary fields, potentially reducing network transfer size and transformation overhead.  
4. **Asynchronous I/O (Asyncio):** For Python environments where thread GIL limitations are a concern (though minimal for I/O-bound tasks like this), refactoring the request functions to use an asynchronous library like aiohttp could offer slightly better performance with higher concurrency limits.

**Projects Used:**

**Apache Accumulo (ACCUMULO)**

**Apache ACE (ACE)**

**Apache ActiveMQ-CPP (AMQCPP)**