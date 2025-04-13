import base64
import logging
import requests
from typing import List, Tuple, Dict, Optional, Any, NamedTuple
from github import Github, GithubException, RateLimitExceededException, UnknownObjectException

class IssueData(NamedTuple):
    number: int
    title: str
    url: str
    labels: List[str]
    body: Optional[str] = None
    comments: List[str] = []

class PullRequestData(NamedTuple):
    number: int
    title: str
    url: str
    body: Optional[str] = None
    comments: List[str] = []

class DiscussionData(NamedTuple):
    number: int
    title: str
    url: str

class DependencyInfo(NamedTuple):
    package_name: str

class GithubRepoInfo(NamedTuple):
    owner: str
    repo_name: str
    description: Optional[str] = None
    stars: Optional[int] = None
    homepage: Optional[str] = None
    languages: Optional[Dict[str, int]] = None
    open_issues: Optional[List[IssueData]] = None
    open_pulls: Optional[List[PullRequestData]] = None
    discussions: Optional[List[DiscussionData]] = None
    dependencies: Optional[List[DependencyInfo]] = None
    error: Optional[str] = None # To report API errors

MAX_ISSUES_FETCH = 20
MAX_PULLS_FETCH = 15
MAX_DISCUSSIONS_FETCH = 15
MAX_COMMENTS_PER_ITEM = 5
ITEM_BODY_TRUNCATE_LEN = 3000 
COMMENT_TRUNCATE_LEN = 500   
GITHUB_API_TIMEOUT = 20    

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def search_repositories(query: str, g: Github, limit: int = 20) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Searches GitHub for repositories."""
    if not query:
        return None, "Search query cannot be empty."
    logging.info(f"Searching repositories for query: '{query}'")
    try:
        # Sort by stars, limit results
        repos = g.search_repositories(query, sort='stars', order='desc')
        results = []
        count = 0
        for repo in repos:
            if count >= limit:
                break
            results.append({
                "full_name": repo.full_name,
                "owner": repo.owner.login,
                "repo_name": repo.name,
                "description": repo.description,
                "language": repo.language,
                "stars": repo.stargazers_count,
                "url": repo.html_url,
                "avatar_url": repo.owner.avatar_url
            })
            count += 1
        logging.info(f"Found {len(results)} repositories for query '{query}'.")
        return results, None
    except RateLimitExceededException:
        logging.error("GitHub API rate limit exceeded during search.")
        return None, "GitHub API rate limit exceeded."
    except GithubException as e:
        logging.error(f"GitHub API Error during search: {e.status} - {e.data.get('message', '')}")
        return None, f"GitHub API Error ({e.status})."
    except Exception as e:
        logging.error(f"Unexpected error during repository search: {e}", exc_info=True)
        return None, "An unexpected error occurred during search."


def get_readme_content(g: Github, owner: str, repo_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetches and decodes README content using the GitHub API."""
    logging.info(f"Fetching README for {owner}/{repo_name} via API...")
    possible_filenames = ["README.md", "readme.md", "README.rst", "README.txt", "README"]
    try:
        repo = g.get_repo(f"{owner}/{repo_name}")
        for filename in possible_filenames:
            try:
                content_file = repo.get_contents(filename)
                if content_file.type == 'file':
                    decoded_content = base64.b64decode(content_file.content).decode('utf-8', errors='replace')
                    logging.info(f"Found and decoded {filename} for {owner}/{repo_name}.")
                    return decoded_content, None
            except UnknownObjectException:
                continue # Try the next filename
            except Exception as e:
                logging.warning(f"Error decoding content for {filename} in {owner}/{repo_name}: {e}")
                continue # Try next file

        logging.info(f"No common README file found in {owner}/{repo_name} root via API.")
        return "No README file found.", None # Explicitly state not found vs error

    except RateLimitExceededException:
        logging.error(f"GitHub API Rate Limit Exceeded fetching README for {owner}/{repo_name}.")
        return None, "Could not fetch README due to rate limits."
    except GithubException as e:
        logging.error(f"GitHub API Error fetching README for {owner}/{repo_name}: {e.status} - {e.data.get('message', '')}")
        # Check for common "Not Found" specifically
        if e.status == 404:
             return None, f"Repository {owner}/{repo_name} not found."
        return None, f"Could not fetch README due to GitHub API error ({e.status})."
    except Exception as e:
        logging.error(f"Unexpected error fetching README via API for {owner}/{repo_name}: {e}", exc_info=True)
        return None, "Unexpected error fetching README."


def run_github_graphql_query(query: str, variables: Dict = None, token: Optional[str] = None) -> Tuple[Optional[Dict], Optional[str]]:
    """Runs a GraphQL query against the GitHub API."""
    if not token:
        return None, "GITHUB_TOKEN required for GraphQL."
    headers = {"Authorization": f"Bearer {token}"}
    api_url = "https://api.github.com/graphql"
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    logging.debug(f"Running GraphQL query: {query[:100]}... with vars: {variables}") # Log query snippet
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=GITHUB_API_TIMEOUT)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        result = response.json()

        if "errors" in result:
            # Check specifically for NOT_FOUND errors which might be expected
            if any(err.get('type') == 'NOT_FOUND' for err in result['errors']):
                logging.info(f"GraphQL query for variables {variables} returned NOT_FOUND.")
                # Depending on context, might return None or specific indicator
                return None, "GraphQL resource not found."
            else:
                error_messages = "; ".join([e.get('message', 'Unknown GraphQL error') for e in result['errors']])
                logging.warning(f"GraphQL Error for variables {variables}: {error_messages}")
                return None, f"GraphQL Error: {error_messages}"

        data = result.get("data")
        if data is None:
            logging.warning(f"GraphQL query returned no 'data' field for variables {variables}. Full response: {result}")
            return None, "GraphQL query returned no data."

        return data, None

    except requests.exceptions.Timeout:
         logging.warning(f"GraphQL request timed out for variables {variables}.")
         return None, "GraphQL request timed out."
    except requests.exceptions.RequestException as e:
         logging.warning(f"GraphQL Request Failed for variables {variables}: {e}")
         return None, f"GraphQL Request Failed: {e}"
    except Exception as e:
         logging.warning(f"Error processing GraphQL response for variables {variables}: {e}", exc_info=True)
         return None, f"Error processing GraphQL response: {e}"


def get_repo_details(owner: str, repo_name: str, g: Github, github_token: Optional[str]) -> GithubRepoInfo:
    """Fetches Languages, Issues, PRs, Dependencies, Discussions via API."""
    logging.info(f"Fetching repository details from GitHub API for {owner}/{repo_name}...")
    languages, open_issues_data, open_pulls_data = None, None, None
    discussions_data, dependencies_data = None, None
    description, stars, homepage = None, None, None
    combined_error = [] # Collect errors

    try:
        # Use the authenticated PyGithub object
        gh_repo = g.get_repo(f"{owner}/{repo_name}")

        # Basic repo info
        description = gh_repo.description
        stars = gh_repo.stargazers_count
        homepage = gh_repo.homepage

        # 1. Languages
        try:
            languages = gh_repo.get_languages()
            logging.info(f"Fetched languages for {owner}/{repo_name}")
        except Exception as e:
            logging.warning(f"Could not fetch languages for {owner}/{repo_name}: {e}")
            combined_error.append("Failed to fetch languages.")

        # 2. Issues (fetch limited number + limited comments)
        open_issues_data = []
        try:
            issues_paginator = gh_repo.get_issues(state='open', sort='updated', direction='desc')
            count = 0
            for issue in issues_paginator:
                if count >= MAX_ISSUES_FETCH: break
                if issue.pull_request is None: # Ensure it's an issue, not a PR
                    comments = []
                    try:
                        comment_paginator = issue.get_comments()
                        comment_count = 0
                        for comment in comment_paginator:
                            if comment_count >= MAX_COMMENTS_PER_ITEM: break
                            if comment.body:
                                comments.append(comment.body[:COMMENT_TRUNCATE_LEN] + ('...' if len(comment.body) > COMMENT_TRUNCATE_LEN else ''))
                            comment_count += 1
                    except RateLimitExceededException:
                         logging.warning(f"Rate limit fetching issue comments for #{issue.number} in {owner}/{repo_name}.")
                         combined_error.append("Rate limit fetching issue comments.")
                         # Allow continuing without comments for this item
                    except Exception as e:
                         logging.warning(f"Could not fetch comments for issue #{issue.number} in {owner}/{repo_name}: {e}")
                         # Allow continuing without comments for this item

                    open_issues_data.append(IssueData(
                        number=issue.number,
                        title=issue.title,
                        url=issue.html_url,
                        labels=[label.name for label in issue.labels],
                        body=issue.body[:ITEM_BODY_TRUNCATE_LEN] + ('...' if issue.body and len(issue.body) > ITEM_BODY_TRUNCATE_LEN else '') if issue.body else None,
                        comments=comments
                    ))
                    count += 1
            logging.info(f"Fetched {len(open_issues_data)} open issues for {owner}/{repo_name}.")
        except RateLimitExceededException:
            logging.error(f"GitHub API Rate Limit Exceeded fetching issues for {owner}/{repo_name}.")
            combined_error.append("Rate limit fetching issues.")
        except Exception as e:
            logging.warning(f"Could not fetch issues for {owner}/{repo_name}: {e}")
            combined_error.append("Failed to fetch issues.")


        # 3. Pull Requests (fetch limited number + limited comments)
        open_pulls_data = []
        try:
            pulls_paginator = gh_repo.get_pulls(state='open', sort='updated', direction='desc')
            count = 0
            for pr in pulls_paginator:
                 if count >= MAX_PULLS_FETCH: break
                 comments = []
                 try:
                    # PRs use issue comments endpoint for review comments too
                    comment_paginator = pr.get_issue_comments()
                    comment_count = 0
                    for comment in comment_paginator:
                        if comment_count >= MAX_COMMENTS_PER_ITEM: break
                        if comment.body:
                             comments.append(comment.body[:COMMENT_TRUNCATE_LEN] + ('...' if len(comment.body) > COMMENT_TRUNCATE_LEN else ''))
                        comment_count += 1
                 except RateLimitExceededException:
                    logging.warning(f"Rate limit fetching PR comments for #{pr.number} in {owner}/{repo_name}.")
                    combined_error.append("Rate limit fetching PR comments.")
                 except Exception as e:
                    logging.warning(f"Could not fetch comments for PR #{pr.number} in {owner}/{repo_name}: {e}")

                 open_pulls_data.append(PullRequestData(
                     number=pr.number,
                     title=pr.title,
                     url=pr.html_url,
                     body=pr.body[:ITEM_BODY_TRUNCATE_LEN] + ('...' if pr.body and len(pr.body) > ITEM_BODY_TRUNCATE_LEN else '') if pr.body else None,
                     comments=comments
                 ))
                 count += 1
            logging.info(f"Fetched {len(open_pulls_data)} open PRs for {owner}/{repo_name}.")
        except RateLimitExceededException:
            logging.error(f"GitHub API Rate Limit Exceeded fetching PRs for {owner}/{repo_name}.")
            combined_error.append("Rate limit fetching PRs.")
        except Exception as e:
            logging.warning(f"Could not fetch PRs for {owner}/{repo_name}: {e}")
            combined_error.append("Failed to fetch PRs.")

    # Catch errors getting the main repo object
    except RateLimitExceededException:
        logging.error(f"GitHub API Rate Limit Exceeded accessing repo {owner}/{repo_name}.")
        combined_error.append("GitHub API Rate Limit Exceeded accessing repository.")
        # Return early as we can't get anything else
        return GithubRepoInfo(owner=owner, repo_name=repo_name, error="; ".join(combined_error))
    except GithubException as e:
        logging.error(f"GitHub API Error accessing repo {owner}/{repo_name}: {e.status} - {e.data.get('message', '')}")
        if e.status == 404:
             combined_error.append(f"Repository {owner}/{repo_name} not found.")
        else:
             combined_error.append(f"GitHub API Error ({e.status}) accessing repository.")
        return GithubRepoInfo(owner=owner, repo_name=repo_name, error="; ".join(combined_error))
    except Exception as e:
        logging.error(f"Unexpected error accessing repo {owner}/{repo_name}: {e}", exc_info=True)
        combined_error.append(f"Unexpected error accessing repository.")
        return GithubRepoInfo(owner=owner, repo_name=repo_name, error="; ".join(combined_error))


    # --- GraphQL parts (Discussions, Dependencies) ---
    # Use the provided github_token for GraphQL
    if github_token:
        # 4. Discussions (GraphQL)
        discussion_query = """
            query($owner: String!, $repo: String!, $num: Int!) {
              repository(owner: $owner, name: $repo) {
                discussions(first: $num, orderBy: {field: UPDATED_AT, direction: DESC}) {
                  nodes {
                    number
                    title
                    url
                  }
                }
              }
            }
        """
        variables = {"owner": owner, "repo": repo_name, "num": MAX_DISCUSSIONS_FETCH}
        gql_result, gql_error = run_github_graphql_query(discussion_query, variables, github_token)
        if gql_error and "resource not found" not in gql_error.lower(): # Ignore not found, it might just mean no discussions enabled/exist
             combined_error.append(f"Failed to fetch discussions: {gql_error}")
        elif gql_result and gql_result.get("repository") and gql_result["repository"].get("discussions"):
            discussions_data = [
                DiscussionData(number=node["number"], title=node["title"], url=node["url"])
                for node in gql_result["repository"]["discussions"].get("nodes", []) if node
            ]
            logging.info(f"Fetched {len(discussions_data)} discussions for {owner}/{repo_name} via GraphQL.")
        else:
            logging.info(f"No discussions found or feature disabled for {owner}/{repo_name} via GraphQL.")


        # 5. Dependencies (GraphQL) - Check if dependency graph is enabled
        dependency_query = """
            query($owner: String!, $repo: String!) {
              repository(owner: $owner, name: $repo) {
                 dependencyGraphManifests(first: 10) { # Check multiple manifests (e.g., package.json, requirements.txt)
                    nodes {
                        dependenciesCount
                        dependencies(first: 100) { # Fetch up to 100 deps per manifest
                           nodes {
                              packageName
                           }
                        }
                    }
                 }
              }
            }
        """
        variables = {"owner": owner, "repo": repo_name}
        gql_result, gql_error = run_github_graphql_query(dependency_query, variables, github_token)

        if gql_error and "resource not found" not in gql_error.lower() and "disabled" not in gql_error.lower():
             combined_error.append(f"Failed to fetch dependencies: {gql_error}")
        elif gql_result and gql_result.get("repository") and gql_result["repository"].get("dependencyGraphManifests"):
             deps = set()
             manifests = gql_result["repository"]["dependencyGraphManifests"].get("nodes", [])
             if not manifests:
                 logging.info(f"Dependency graph may be disabled or no manifests found for {owner}/{repo_name}.")
             else:
                 for manifest in manifests:
                      if manifest and manifest.get("dependencies"):
                           for dep in manifest["dependencies"].get("nodes", []):
                                if dep and dep.get("packageName"):
                                     deps.add(dep["packageName"])
                 dependencies_data = [DependencyInfo(package_name=name) for name in sorted(list(deps))]
                 logging.info(f"Fetched {len(dependencies_data)} unique dependencies for {owner}/{repo_name} via GraphQL.")
        else:
             # This case can happen if the dependency graph feature is disabled for the repo
             logging.info(f"Dependency graph likely disabled or no dependencies found for {owner}/{repo_name}.")

    else:
        logging.warning("No GITHUB_TOKEN provided, skipping GraphQL queries (Discussions, Dependencies).")
        combined_error.append("Skipped fetching Discussions/Dependencies (no token).")


    return GithubRepoInfo(
        owner=owner,
        repo_name=repo_name,
        description=description,
        stars=stars,
        homepage=homepage,
        languages=languages,
        open_issues=open_issues_data if open_issues_data is not None else [], # Ensure list type
        open_pulls=open_pulls_data if open_pulls_data is not None else [],   # Ensure list type
        discussions=discussions_data if discussions_data is not None else [], # Ensure list type
        dependencies=dependencies_data if dependencies_data is not None else [],# Ensure list type
        error="; ".join(combined_error) if combined_error else None
    )