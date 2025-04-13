import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from github import Github, GithubException, RateLimitExceededException
# from database import db
from config import AppConfig
from core.utils import parse_github_url
from core import github_api
from core import gemini_api

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Flask App
app = Flask(__name__)
app.config.from_object(AppConfig)

# Enable CORS for requests from frontend origin (adjust in production)
CORS(app, resources={r"/api/*": {"origins": [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",  # Add Vite's default port
    "http://127.0.0.1:5173"   # Add Vite's localhost alternative
]}})

# Uncomment and configure if using SQLAlchemy
# app.config['SQLALCHEMY_DATABASE_URI'] = AppConfig.DATABASE_URL
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# db.init_app(app)

# --- Helper Functions ---
def get_github_instance():
    """Creates an authenticated PyGithub instance."""
    token = AppConfig.GITHUB_TOKEN
    if not token:
        raise ValueError("GITHUB_TOKEN is not configured.")
    try:
        # Increase timeout for potentially long operations
        return Github(token, timeout=github_api.GITHUB_API_TIMEOUT + 5) # Slightly longer than internal timeout
    except Exception as e:
        logging.error(f"Failed to initialize Github instance: {e}")
        raise ConnectionError("Could not connect to GitHub API.")

def convert_namedtuples_in_list_to_dicts(data_list):
    """Converts a list of NamedTuple objects to a list of dictionaries."""
    if data_list is None:
        # Return None or empty list based on desired JSON output for null input
        return None
    return [item._asdict() for item in data_list if hasattr(item, '_asdict')]



# --- API Endpoints ---
@app.route('/api/search', methods=['GET'])
def search_repos():
    """Endpoint to search for GitHub repositories."""
    query = request.args.get('q', '')
    if not query:
        return jsonify({"error": "Search query parameter 'q' is required."}), 400

    try:
        g = get_github_instance()
        results, error = github_api.search_repositories(query, g, limit=20) # Limit search results
        if error:
            # Determine appropriate status code based on error
            status_code = 429 if "rate limit" in error.lower() else 500
            return jsonify({"error": error}), status_code
        return jsonify(results or []), 200 # Return empty list if no results

    except ValueError as e: # Raised by get_github_instance if no token
        return jsonify({"error": str(e)}), 500
    except ConnectionError as e:
        return jsonify({"error": str(e)}), 503 # Service Unavailable
    except Exception as e:
        logging.exception("Unexpected error in /api/search") # Log full traceback
        return jsonify({"error": f"An unexpected server error occurred: {e}"}), 500


@app.route('/api/analyze/<owner>/<repo_name>', methods=['GET'])
def analyze_repo(owner, repo_name):
    """Endpoint to fetch core repository data (README, details)."""
    if not owner or not repo_name:
        return jsonify({"error": "Owner and repository name are required."}), 400

    logging.info(f"Received analysis request for {owner}/{repo_name}")
    try:
        g = get_github_instance()
        github_token = AppConfig.GITHUB_TOKEN

        readme_content, readme_error = github_api.get_readme_content(g, owner, repo_name)
        repo_data = github_api.get_repo_details(owner, repo_name, g, github_token)

        repo_data_dict = {}
        combined_errors = [] 

        if readme_error:
            combined_errors.append(f"README Fetch Error: {readme_error}")
            if "not found" in readme_error.lower():
                 return jsonify({"error": f"Repository {owner}/{repo_name} not found."}), 404

        if repo_data:
            # Start with the top-level dict conversion
            repo_data_dict = repo_data._asdict()
            # Now, explicitly convert nested lists
            repo_data_dict['open_issues'] = convert_namedtuples_in_list_to_dicts(repo_data.open_issues)
            repo_data_dict['open_pulls'] = convert_namedtuples_in_list_to_dicts(repo_data.open_pulls)
            repo_data_dict['discussions'] = convert_namedtuples_in_list_to_dicts(repo_data.discussions)
            repo_data_dict['dependencies'] = convert_namedtuples_in_list_to_dicts(repo_data.dependencies)
            # languages should already be a dict, error is a string/None

            if repo_data.error:
                combined_errors.append(f"Repo Details Fetch Error: {repo_data.error}")
                if "not found" in repo_data.error.lower():
                     # This might be redundant if caught by readme check, but safe
                    return jsonify({"error": f"Repository {owner}/{repo_name} not found."}), 404
        else:
            # Handle case where get_repo_details itself might return None (though unlikely with current structure)
            logging.error(f"get_repo_details returned None for {owner}/{repo_name}")
            combined_errors.append("Failed to retrieve core repository details.")
            # Decide how to proceed, maybe return 500?


        response_data = {
            "readme": readme_content,
            "repo_data": repo_data_dict, # Use the dictionary with nested dicts
            "fetch_errors": combined_errors if combined_errors else None
        }

        status_code = 200
        if combined_errors:
            logging.warning(f"Partial errors fetching data for {owner}/{repo_name}: {combined_errors}")
            # If repo_data_dict is empty AND we have errors, maybe it's a 500 or 404?
            if not repo_data_dict and any("not found" in e.lower() for e in combined_errors):
                 status_code = 404
                 response_data["error"] = f"Repository {owner}/{repo_name} not found or major fetch error."


        logging.info(f"Successfully prepared analysis data for {owner}/{repo_name} (with potential partial errors).")
        # jsonify will now correctly handle the list of dictionaries
        return jsonify(response_data), status_code

    except ValueError as e: # No GitHub token
        return jsonify({"error": str(e)}), 500
    except ConnectionError as e: # Cannot connect to GitHub
        return jsonify({"error": str(e)}), 503
    except GithubException as e: # PyGithub specific errors
        logging.exception(f"GitHub API error during analysis for {owner}/{repo_name}")
        status_code = e.status if hasattr(e, 'status') else 500
        err_msg = e.data.get('message', 'Unknown') if hasattr(e, 'data') else str(e)
        return jsonify({"error": f"GitHub API Error ({status_code}): {err_msg}"}), status_code
    except Exception as e: # Catch-all
        logging.exception(f"Unexpected error in /api/analyze/{owner}/{repo_name}")
        return jsonify({"error": f"An unexpected server error occurred: {e}"}), 500
    
@app.route('/api/summarize/overall/<owner>/<repo_name>', methods=['POST'])
def summarize_overall(owner, repo_name):
    """Endpoint to generate the overall AI repository summary."""
    if not owner or not repo_name:
        return jsonify({"error": "Owner and repository name are required."}), 400

    # Expect readme and repo_data in the POST body
    data = request.get_json()
    if not data or 'readme' not in data or 'repo_data' not in data:
        return jsonify({"error": "Missing 'readme' or 'repo_data' in request body."}), 400

    readme_text = data.get('readme')
    
    # Handle potential missing fields if needed, provide defaults
    repo_info_dict = data.get('repo_data', {})
    
    # Reconstruct GithubRepoInfo from the dict received from frontend/cache
    try:
        issues_data_from_payload = repo_info_dict.get('open_issues')
        logging.debug(f"Received issues data type: {type(issues_data_from_payload)}") # Add logging
        if issues_data_from_payload and isinstance(issues_data_from_payload, list) and len(issues_data_from_payload) > 0:
            logging.debug(f"First element type: {type(issues_data_from_payload[0])}")
            logging.debug(f"First element value preview: {str(issues_data_from_payload[0])[:200]}") # Log preview

        issues_list = []
        if issues_data_from_payload and isinstance(issues_data_from_payload, list): # Ensure it's a list
            for index, issue_item in enumerate(issues_data_from_payload): # Use a different var name temporarily
                logging.debug(f"Processing issue_item at index {index}, type: {type(issue_item)}") # Log type inside loop

                if not isinstance(issue_item, dict):
                    logging.error(f"Unexpected type found in 'open_issues' list at index {index}: {type(issue_item)}. Value: {issue_item}. Skipping.")
                    continue # Skip this malformed item and proceed to the next

                # Now we know issue_item is a dictionary, rename for clarity if desired
                issue_dict = issue_item

                try:
                    # Ensure 'comments' exists and is a list (handle None explicitly)
                    comments_val = issue_dict.get('comments')
                    if comments_val is None:
                        issue_dict['comments'] = []
                    elif not isinstance(comments_val, list):
                        logging.warning(f"Issue #{issue_dict.get('number','N/A')} comments field was not None or list, type: {type(comments_val)}. Setting to empty list.")
                        issue_dict['comments'] = []
                    # else: it's already a list, do nothing

                    # Create the IssueData object
                    # Add extra validation before creating IssueData if needed
                    issues_list.append(github_api.IssueData(**issue_dict))

                except TypeError as te:
                    # This catches errors if issue_dict is missing keys required by IssueData
                    logging.error(f"Failed to create IssueData from dict at index {index}: {issue_dict}. Error: {te}")
                    # Optionally 'continue' here to skip adding this problematic issue

        # Use the potentially filtered/corrected issues_list
        repo_info_dict['open_issues'] = issues_list

        # --- Apply similar loops/checks for pulls, discussions, dependencies ---

        # Reconstruct open_pulls list
        pulls_list = []
        pulls_data_from_payload = repo_info_dict.get('open_pulls')
        if pulls_data_from_payload and isinstance(pulls_data_from_payload, list):
            for index, pr_item in enumerate(pulls_data_from_payload):
                if not isinstance(pr_item, dict):
                    logging.error(f"Unexpected type found in 'open_pulls' list at index {index}: {type(pr_item)}. Skipping.")
                    continue
                pr_dict = pr_item
                try:
                    comments_val = pr_dict.get('comments')
                    pr_dict['comments'] = [] if comments_val is None or not isinstance(comments_val, list) else comments_val
                    pulls_list.append(github_api.PullRequestData(**pr_dict))
                except TypeError as te:
                    logging.error(f"Failed to create PullRequestData from dict at index {index}: {pr_dict}. Error: {te}")
        repo_info_dict['open_pulls'] = pulls_list

        # Reconstruct discussions list (simpler structure usually)
        discussions_list = []
        discussions_data_from_payload = repo_info_dict.get('discussions')
        if discussions_data_from_payload and isinstance(discussions_data_from_payload, list):
            for index, disc_item in enumerate(discussions_data_from_payload):
                if not isinstance(disc_item, dict):
                    logging.error(f"Unexpected type found in 'discussions' list at index {index}: {type(disc_item)}. Skipping.")
                    continue
                disc_dict = disc_item
                try:
                    discussions_list.append(github_api.DiscussionData(**disc_dict))
                except TypeError as te:
                    logging.error(f"Failed to create DiscussionData from dict at index {index}: {disc_dict}. Error: {te}")
        repo_info_dict['discussions'] = discussions_list

        # Reconstruct dependencies list (simpler structure usually)
        dependencies_list = []
        dependencies_data_from_payload = repo_info_dict.get('dependencies')
        if dependencies_data_from_payload and isinstance(dependencies_data_from_payload, list):
            for index, dep_item in enumerate(dependencies_data_from_payload):
                if not isinstance(dep_item, dict):
                    logging.error(f"Unexpected type found in 'dependencies' list at index {index}: {type(dep_item)}. Skipping.")
                    continue
                dep_dict = dep_item
                try:
                    dependencies_list.append(github_api.DependencyInfo(**dep_dict))
                except TypeError as te:
                    logging.error(f"Failed to create DependencyInfo from dict at index {index}: {dep_dict}. Error: {te}")
        repo_info_dict['dependencies'] = dependencies_list


        # Now construct the main GithubRepoInfo object using the modified dict
        # Ensure all expected top-level keys are present or provide defaults
        repo_info = github_api.GithubRepoInfo(
            owner=repo_info_dict.get('owner'),
            repo_name=repo_info_dict.get('repo_name'),
            description=repo_info_dict.get('description'),
            stars=repo_info_dict.get('stars'),
            homepage=repo_info_dict.get('homepage'),
            languages=repo_info_dict.get('languages'),
            open_issues=repo_info_dict.get('open_issues', []), 
            open_pulls=repo_info_dict.get('open_pulls', []),   
            discussions=repo_info_dict.get('discussions', []), 
            dependencies=repo_info_dict.get('dependencies', []),
            error=repo_info_dict.get('error')
        )

    except (TypeError, KeyError) as e:
        logging.error(f"Could not reconstruct GithubRepoInfo from POST data: {e}. Data received: {data.get('repo_data')}")
        return jsonify({"error": f"Invalid 'repo_data' structure received: {e}"}), 400
    
    logging.info(f"Generating overall summary for {owner}/{repo_name}")
    try:
        summary, error = gemini_api.generate_overall_repo_summary(readme_text, repo_info)
        if error:
            # Determine status code (e.g., 429 for rate limit, 503 for unavailable, 500 internal)
             status_code = 503 if "Gemini API key" in error else 500 # Basic check
             if "blocked" in error.lower(): status_code = 400 # Bad request due to safety
             return jsonify({"error": error}), status_code

        logging.info(f"Successfully generated overall summary for {owner}/{repo_name}")
        return jsonify({"summary": summary}), 200
    except Exception as e:
        logging.exception(f"Unexpected error generating overall summary for {owner}/{repo_name}")
        return jsonify({"error": f"An unexpected server error occurred during summary generation: {e}"}), 500


@app.route('/api/summarize/item/<owner>/<repo_name>/<item_type>/<int:item_number>', methods=['POST'])
def summarize_item(owner, repo_name, item_type, item_number):
    """Endpoint to generate AI summary for a specific issue or PR."""
    if item_type not in ['issue', 'pull_request']:
        return jsonify({"error": "Invalid item_type. Must be 'issue' or 'pull_request'."}), 400

    # Expect minimal item data (title, body, comments) in POST body
    item_data = request.get_json()
    if not item_data or 'title' not in item_data: # Basic check
        return jsonify({"error": "Missing item data (at least 'title') in request body."}), 400

    # Add number for context, even if not strictly needed by Gemini function yet
    item_data['number'] = item_number

    logging.info(f"Generating summary for {item_type} #{item_number} in {owner}/{repo_name}")
    try:
        summary, error = gemini_api.summarize_github_item(item_data, item_type)
        if error:
             status_code = 503 if "Gemini API key" in error else 500
             if "blocked" in error.lower(): status_code = 400
             return jsonify({"error": error}), status_code

        logging.info(f"Successfully generated item summary for {item_type} #{item_number} in {owner}/{repo_name}")
        return jsonify({"summary": summary}), 200
    except Exception as e:
        logging.exception(f"Unexpected error generating item summary for {item_type} #{item_number}")
        return jsonify({"error": f"An unexpected server error occurred during item summary generation: {e}"}), 500


@app.route('/api/analyze/error/<owner>/<repo_name>', methods=['POST'])
def analyze_error_endpoint(owner, repo_name):
    """Endpoint to analyze a user's error message."""
    data = request.get_json()
    if not data or 'error_message' not in data:
        return jsonify({"error": "Missing 'error_message' in request body."}), 400

    user_error = data.get('error_message')
    # Expect issues and dependencies data (or relevant parts) also in the body
    # Reconstruct minimal needed structures
    issues_data = data.get('issues', [])
    dependencies_data = data.get('dependencies', [])

    # Convert dicts back to NamedTuples if needed by the function, or adapt function
    try:
        issues = [github_api.IssueData(**issue) for issue in issues_data] if issues_data else None
        dependencies = [github_api.DependencyInfo(**dep) for dep in dependencies_data] if dependencies_data else None
    except TypeError as e:
        logging.error(f"Could not reconstruct IssueData/DependencyInfo from POST data: {e}")
        return jsonify({"error": f"Invalid 'issues' or 'dependencies' structure in request body: {e}"}), 400

    logging.info(f"Generating error analysis for {owner}/{repo_name}")
    try:
        analysis, error = gemini_api.analyze_user_error(user_error, issues, dependencies, repo_name)
        if error:
            status_code = 503 if "Gemini API key" in error else 500
            if "blocked" in error.lower(): status_code = 400
            return jsonify({"error": error}), status_code

        logging.info(f"Successfully generated error analysis for {owner}/{repo_name}")
        return jsonify({"analysis": analysis}), 200
    except Exception as e:
        logging.exception(f"Unexpected error generating error analysis for {owner}/{repo_name}")
        return jsonify({"error": f"An unexpected server error occurred during error analysis: {e}"}), 500


# --- Main Execution ---
if __name__ == '__main__':
    # Use Flask's built-in server for development
    # Debug will be true if FLASK_ENV=development
    app.run(host='0.0.0.0', port=5001) 