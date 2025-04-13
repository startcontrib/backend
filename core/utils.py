from urllib.parse import urlparse
from typing import Optional, Tuple

def parse_github_url(url: str) -> Optional[Tuple[str, str]]:
    """Parses GitHub URL to extract owner and repo name."""
    if not url:
        return None
    try:
        # Ensure scheme is present for urlparse
        if not url.startswith(('http://', 'https://')):
             if url.startswith('git@'): # Handle SSH protocol format
                 path_part = url.split(':')[-1]
                 path_parts = path_part.split('/')
                 if len(path_parts) >= 2:
                     owner = path_parts[0]
                     repo_name = path_parts[1]
                     if repo_name.endswith('.git'):
                         repo_name = repo_name[:-4]
                     return owner, repo_name
                 else: return None
             else: # Assume https if missing
                 url = 'https://' + url

        parsed = urlparse(url)
        # Allow github.com and potentially enterprise instances if needed later
        if 'github.com' in parsed.netloc:
            path_parts = parsed.path.strip('/').split('/')
            if len(path_parts) >= 2:
                owner, repo_name = path_parts[0], path_parts[1]
                # Remove trailing .git if present
                if repo_name.endswith('.git'):
                    repo_name = repo_name[:-4]
                return owner, repo_name
    except Exception: # Catch potential parsing errors
        return None
    return None

