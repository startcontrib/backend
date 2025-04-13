# core/gemini_api.py
import logging
import google.generativeai as genai
from typing import List, Tuple, Dict, Optional, Any, NamedTuple
from config import AppConfig
from core.github_api import IssueData, PullRequestData, DependencyInfo, GithubRepoInfo # Use structures

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
README_TRUNCATE_LEN = 6000
ITEM_BODY_TRUNCATE_LEN_SUMMARY = 2500 # Slightly less than API fetch limit for summary context
MAX_TOKENS_OVERALL_SUMMARY = 800
MAX_TOKENS_ITEM_SUMMARY = 300
MAX_TOKENS_ERROR_ANALYSIS = 600
GEMINI_TEMPERATURE_OVERALL = 0.6
GEMINI_TEMPERATURE_ITEM = 0.4
GEMINI_TEMPERATURE_ERROR = 0.5


def call_gemini_api(prompt: str, max_tokens: int, temperature: float) -> Tuple[Optional[str], Optional[str]]:
    """Calls the Gemini API with the given prompt and parameters."""
    api_key = AppConfig.GEMINI_API_KEY
    if not api_key:
        logging.error("Gemini API key not configured.")
        return None, "Gemini API key not configured."

    logging.info(f"Calling Gemini API (max_tokens={max_tokens}, temp={temperature}). Prompt length: {len(prompt)}")
    logging.debug(f"Gemini Prompt Snippet: {prompt[:200]}...") # Log beginning of prompt

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash-latest') # Use the recommended model
        generation_config = genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            top_p=1.0 # Keep top_p at 1.0 unless specific need arises
        )
        # Standard safety settings
        safety_settings = [
            {"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in [
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            ]
        ]

        response = model.generate_content(
            prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        if response.candidates:
            first_candidate = response.candidates[0]
            if first_candidate.content and first_candidate.content.parts:
                result_text = first_candidate.content.parts[0].text
                logging.info("Gemini API call successful.")
                logging.debug(f"Gemini Response Snippet: {result_text[:200]}...")
                return result_text, None
            else:
                # Investigate finish reason if content is empty
                finish_reason = getattr(first_candidate, 'finish_reason', "Unknown")
                safety_ratings = getattr(first_candidate, 'safety_ratings', [])
                logging.warning(f"Gemini response candidate was empty. Finish reason: {finish_reason}, Safety: {safety_ratings}")
                # Check if it was blocked by safety
                if finish_reason == 'SAFETY':
                     blocked_reasons = [r.category for r in safety_ratings if r.probability != 'NEGLIGIBLE']
                     return None, f"Gemini response blocked due to safety concerns: {', '.join(blocked_reasons)}"
                # Check if it stopped due to token limits
                elif finish_reason == 'MAX_TOKENS':
                     return None, "Gemini response stopped due to reaching the maximum output token limit."
                else:
                     return None, f"Gemini response was empty or incomplete (Finish Reason: {finish_reason})."
        else:
            # Check prompt feedback for blocking reasons
            feedback_reason = getattr(response.prompt_feedback, 'block_reason', None)
            safety_ratings = getattr(response.prompt_feedback, 'safety_ratings', [])
            if feedback_reason:
                blocked_reasons = [r.category for r in safety_ratings if r.probability != 'NEGLIGIBLE']
                logging.error(f"Gemini API prompt blocked. Reason: {feedback_reason}. Categories: {', '.join(blocked_reasons)}")
                return None, f"Gemini prompt blocked due to safety concerns: {feedback_reason} ({', '.join(blocked_reasons)})"
            else:
                logging.error("Gemini API call returned no candidates and no specific blocking reason.")
                return None, "Gemini API call returned no candidates."

    except Exception as e:
        logging.error(f"Error calling Gemini API: {e}", exc_info=True)
        # Try to give more specific feedback if possible (e.g., API key issues)
        if "API key not valid" in str(e):
            return None, "Invalid Gemini API key."
        return None, f"An unexpected error occurred while communicating with the AI model: {e}"


def generate_overall_repo_summary(readme_text: Optional[str], github_info: GithubRepoInfo) -> Tuple[Optional[str], Optional[str]]:
    """Generates an overall summary using Gemini."""
    logging.info(f"Generating overall summary for {github_info.owner}/{github_info.repo_name}")

    # Prepare README snippet
    readme_snippet = "README content not available or could not be read.\n"
    if readme_text and readme_text != "No README file found.": # Check for specific non-found string
        readme_snippet = readme_text[:README_TRUNCATE_LEN] + ("..." if len(readme_text) > README_TRUNCATE_LEN else "")

    # Prepare activity context from titles only (keep it concise)
    activity_context = ""
    if github_info.open_issues:
        activity_context += f"Recent Open Issues ({len(github_info.open_issues)} fetched):\n" + "\n".join([
            # Directly access attributes now, ensure labels is handled correctly
            f"- #{issue.number}: {issue.title} (Labels: {', '.join(issue.labels) if issue.labels else 'None'})"
            for issue in github_info.open_issues[:10] # Limit context
        ]) + "\n\n"
    else:
        activity_context += "No recent open issues fetched or available.\n\n"

    if github_info.open_pulls:
        activity_context += f"Recent Open PRs ({len(github_info.open_pulls)} fetched):\n" + "\n".join([
            # Directly access attributes
            f"- #{p.number}: {p.title}"
            for p in github_info.open_pulls[:10] # Limit context
        ]) + "\n\n"
    else:
        activity_context += "No recent open PRs fetched or available.\n\n"

    # Prepare future context - SIMPLIFIED
    future_context = ""
    if github_info.discussions:
        future_context += f"Recent Discussions ({len(github_info.discussions)} fetched):\n" + "\n".join([
             # Directly access attributes
            f"- #{d.number}: {d.title}"
            for d in github_info.discussions[:10] # Limit context
        ]) + "\n"
    else:
        future_context += "No recent discussions fetched or available.\n"
        
    # Construct the prompt
    prompt = (
        "You are an expert software analyst providing a high-level overview of a GitHub repository.\n\n"
        f"== Repository Context ({github_info.owner}/{github_info.repo_name}) ==\n"
        f"Description: {github_info.description}\n"
        f"Homepage: {github_info.homepage}\n"
        f"Stars: {github_info.stars}\n\n"
        f"README Snippet:\n```\n{readme_snippet}\n```\n---\n"
        f"Recent Activity (Titles & Labels):\n{activity_context}\n---\n"
        f"Recent Discussions (Titles):\n{future_context}\n---\n\n"
        "== Analysis Task ==\n"
        "Based *only* on the provided context (description, homepage, stars, README snippet, recent issue/PR/discussion titles), generate a concise overview covering:\n"
        "1.  **Project Purpose & Use Cases:** What problem does this project likely solve? Who is it for? Potential use cases?\n"
        "2.  **Current Activity:** Summarize the apparent focus of development based on open issues/PRs.\n"
        "3.  **Future Directions / Community Topics:** Hints from discussion titles about planned features, major changes, or community focus?\n\n"
        "Format the output using Markdown. Be objective and concise. If context is missing for a section, state that."
    )

    return call_gemini_api(prompt, max_tokens=MAX_TOKENS_OVERALL_SUMMARY, temperature=GEMINI_TEMPERATURE_OVERALL)


def summarize_github_item(item_data: Dict[str, Any], item_type: str) -> Tuple[Optional[str], Optional[str]]:
    """Summarizes a GitHub Issue or Pull Request using Gemini."""
    item_number = item_data.get("number", "N/A")
    logging.info(f"Generating summary for {item_type} #{item_number}")

    # Extract data safely
    title = item_data.get("title", "No Title")
    body = item_data.get("body")
    comments = item_data.get("comments", [])

    if not body and not comments:
        logging.info(f"No body or comments found for {item_type} #{item_number}. Skipping summary.")
        return f"No body or comments available to summarize for {item_type} #{item_number}.", None

    # Prepare context
    context = f"Title: {title}\n\n"
    if body:
        context += f"Body:\n```\n{body[:ITEM_BODY_TRUNCATE_LEN_SUMMARY]}\n```\n\n" # Use specific truncate len for summary
    if comments:
        context += "Recent Comments Snippets:\n---\n" + "\n---\n".join(comments) + "\n---\n" # Comments already truncated

    # Construct prompt
    prompt = (
        f"You are an expert summarizer analyzing a GitHub {item_type} discussion.\n\n"
        f"== GitHub {item_type} #{item_number} Context ==\n{context}\n== End of Context ==\n\n"
        "== Analysis Task ==\n"
        f"Summarize the core topic or problem discussed in this {item_type} based *only* on the provided Title, Body snippet, and Comment snippets.\n"
        "**Crucially, look through the comments for hints of:**\n"
        "- Potential solutions or workarounds proposed.\n"
        "- Decisions made.\n"
        "- Links to related issues/PRs or external resources.\n"
        "- Key questions being asked.\n"
        "Mention any findings briefly.\n\n"
        f"Provide a concise summary (typically 2-4 sentences) in plain text:"
    )

    return call_gemini_api(prompt, max_tokens=MAX_TOKENS_ITEM_SUMMARY, temperature=GEMINI_TEMPERATURE_ITEM)


def analyze_user_error(
    user_error: str,
    issues: Optional[List[IssueData]],
    dependencies: Optional[List[DependencyInfo]],
    repo_name: str
) -> Tuple[Optional[str], Optional[str]]:
    """Analyzes user error against issues and dependencies using Gemini."""
    logging.info(f"Generating error analysis for repo '{repo_name}'")
    if not user_error:
        return "Please provide an error message or description.", None

    # 1. Prepare Issue Context (limited info)
    issue_context = "No relevant open issues found or fetched for context.\n"
    if issues:
        # Limit the number of issues passed to the prompt for context window and relevance
        issues_to_consider = issues[:15] # Use MAX_ITEMS_TO_DISPLAY or a fixed number
        issue_summaries = [
            f"- Issue #{issue.number} (Labels: {', '.join(issue.labels) if issue.labels else 'None'}): {issue.title}\n   *Body Snippet:* {issue.body[:150] + '...' if issue.body and len(issue.body) > 150 else issue.body or '(No body snippet)'}"
            for issue in issues_to_consider
        ]
        if issue_summaries:
            issue_context = f"Potential Relevant Open Issues (Titles & Snippets from '{repo_name}'):\n" + "\n".join(issue_summaries) + "\n"

    # 2. Prepare Dependency Context
    dependency_context = "Dependency information not available or not fetched.\n"
    if dependencies:
        dependency_context = f"Project Dependencies (for reference, check if error mentions these): {', '.join([d.package_name for d in dependencies])}\n"

    # 3. Construct Prompt
    prompt = (
        f"You are a helpful debugging assistant analyzing a user's error message potentially related to the '{repo_name}' GitHub repository.\n\n"
        "== User's Error / Problem Description ==\n"
        f"```\n{user_error}\n```\n\n"
        f"== Repository Context (Limited View) ==\n"
        f"{issue_context}\n"
        f"{dependency_context}\n"
        "---\n\n"
        "== Analysis Task ==\n"
        "Based *only* on the user's error description and the provided repository context (a list of recent open issue titles/snippets, and project dependencies), perform the following analysis:\n"
        "1.  **Keyword Match & Similarity:** Does the user's error message contain keywords or patterns that appear similar to any of the listed open issue titles or snippets? If yes, mention the potentially related issue number(s) (e.g., '#123') and briefly explain *why* they *might* be related (e.g., 'mentions similar function name', 'describes similar behavior'). Be cautious, as snippets are short.\n"
        "2.  **Dependency Check:** Does the error message explicitly mention any package names listed in the Project Dependencies? If yes, suggest the user check the documentation or issue tracker for that specific dependency.\n"
        "3.  **General Guidance (If No Clear Match):** If no obvious connection is found in the provided context, provide brief, generic troubleshooting suggestions like:\n    *   Checking the official '{repo_name}' documentation.\n    *   Searching '{repo_name}' closed issues on GitHub.\n    *   Verifying their environment setup (versions, configurations).\n    *   Considering if the issue might be in a related library not listed in the primary dependencies.\n"
        "4.  **Crucial Disclaimer:** Start your response with this exact disclaimer: '**Disclaimer:** This analysis is based on keyword matching between your error and limited repository metadata (issue titles/snippets, dependencies). It **cannot** analyze the repository's source code, understand execution context, or guarantee accuracy. It's a starting point for your own investigation.'\n\n"
        "Present the analysis clearly using Markdown. Be concise and focus on actionable next steps for the user."
    )

    return call_gemini_api(prompt, max_tokens=MAX_TOKENS_ERROR_ANALYSIS, temperature=GEMINI_TEMPERATURE_ERROR)