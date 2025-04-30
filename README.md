# GitHub Repository Analyzer API

A Flask-based backend API that fetches GitHub repository data and uses Google's Gemini AI to generate insightful summaries.

## Features

- Search GitHub repositories by keywords
- Retrieve repository details (README, open issues, PRs, discussions, dependencies)
- Generate AI-powered summaries of repositories
- Summarize individual issues and pull requests
- Analyze error messages in the context of a repository

## Technologies

- Python 3.x
- Flask web framework
- PyGithub for GitHub API integration
- Google's Gemini AI for summaries
- GitHub GraphQL API for certain data points

## Installation

1. Clone the repository
2. Create a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Copy the `.env_example` file to `.env` and fill in the required API keys:
   ```
   cp env_example .env
   ```

## Environment Variables

Required environment variables in your `.env` file:

- `GITHUB_TOKEN`: Your GitHub Personal Access Token
- `GEMINI_API_KEY`: Your Google Gemini API key
- `SECRET_KEY`: A secret key for Flask
- `DATABASE_URL`: (Optional) For future database implementation

## API Endpoints

### Repository Search
`GET /api/search?q=<query>`
- Searches GitHub for repositories matching the query
- Returns repository basic information ordered by stars

### Repository Analysis
`GET /api/analyze/<owner>/<repo_name>`
- Fetches detailed repository information including README, open issues, PRs and discussions

### Generate Repository Summary
`POST /api/summarize/overall/<owner>/<repo_name>`
- Generates an AI-powered overview of the repository
- Requires repository data in the request body

### Summarize Issues/PRs
`POST /api/summarize/item/<owner>/<repo_name>/<item_type>/<item_number>`
- Generates a concise summary of an issue or pull request
- Item type can be "issue" or "pr"

### Error Analysis
`POST /api/analyze/error/<owner>/<repo_name>`
- Analyzes user-reported error messages in the context of the repository

## Project Structure

- `app.py`: Main Flask application with API endpoints
- `config.py`: Configuration management and environment variables
- `database.py`: Database setup (for future implementations)
- `core/`: Core functionality modules
  - `github_api.py`: GitHub API integration
  - `gemini_api.py`: Google Gemini AI integration
  - `utils.py`: Utility functions

## Error Handling

The API implements comprehensive error handling for:
- GitHub API rate limits
- Authentication issues
- Missing resources
- AI response errors
- General server exceptions

## Future Enhancements

- Database integration for caching and history
- User authentication
- Additional AI-powered insights
- Support for more GitHub data sources 