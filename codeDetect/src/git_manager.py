"""
Git management module for extracting repository context and changes.
Supports both local repositories and GitHub URLs with token authentication.
"""

import os
import datetime
import tempfile
import shutil
import time
import re
from typing import List, Dict, Optional, Any
from git import Repo, InvalidGitRepositoryError, GitCommandError


class GitManager:
    """
    Manages git repository access with proper validation and error handling.
    Supports both local paths and GitHub URLs with token authentication.
    """

    def __init__(self, repo_path: str, github_token: Optional[str] = None, branch: str = "main"):
        """
        Initialize GitManager with repository path or GitHub URL.

        Args:
            repo_path: Local path to git repository or GitHub URL (https://github.com/owner/repo)
            github_token: GitHub personal access token for private repositories
            branch: Branch to clone (default: main)

        Raises:
            InvalidGitRepositoryError: If path is not a valid git repository
        """
        self.is_temporary = False
        self.temp_dir = None
        self.source_repo_input = repo_path
        self.repo_slug: Optional[str] = None

        # Check if repo_path is a GitHub URL
        if repo_path.startswith(('http://', 'https://', 'git@')):
            self.is_temporary = True
            self.temp_dir = tempfile.mkdtemp(prefix='git_clone_')
            self.repo_slug = self._extract_repo_slug(repo_path)

            # Build authenticated URL if token provided
            if github_token and repo_path.startswith('https://github.com/'):
                # Insert token into URL: https://TOKEN@github.com/owner/repo
                auth_url = repo_path.replace('https://github.com/', f'https://{github_token}@github.com/')
            else:
                auth_url = repo_path

            last_error: Optional[Exception] = None
            for attempt in range(1, 4):
                try:
                    print(f"Cloning repository from {repo_path} (attempt {attempt}/3)...")
                    self.repo = Repo.clone_from(auth_url, self.temp_dir, branch=branch)
                    self.repo_path = self.temp_dir
                    print(f"Repository cloned to temporary directory: {self.temp_dir}")
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    if attempt < 3:
                        time.sleep(1.5 * attempt)
            if last_error is not None:
                if self.temp_dir and os.path.exists(self.temp_dir):
                    shutil.rmtree(self.temp_dir)
                error_text = str(last_error)
                if github_token:
                    error_text = error_text.replace(github_token, "***")
                raise InvalidGitRepositoryError(
                    f"Failed to clone repository from {repo_path}: {error_text}"
                )
        else:
            # Local repository path
            self.repo_path = repo_path

            # Validate .git directory exists
            git_dir = os.path.join(repo_path, ".git")
            if not os.path.exists(git_dir):
                raise InvalidGitRepositoryError(
                    f"No .git folder found at {repo_path}. "
                    "Please run this tool in a git repository."
                )

            try:
                self.repo = Repo(repo_path)
            except Exception as e:
                raise InvalidGitRepositoryError(
                    f"Failed to initialize git repository: {e}"
                )

        # Validate repository has at least one commit
        if self.repo.head.is_detached:
            self._current_branch = "DETACHED HEAD"
        else:
            try:
                self._current_branch = self.repo.active_branch.name
            except TypeError:
                self._current_branch = "unknown"

    def get_metadata(self) -> Dict[str, Any]:
        """
        Extract repository metadata for impact_report.json.

        Returns:
            Dictionary containing repository context
        """
        metadata: Dict[str, Any] = {
            "repository": os.path.basename(os.path.abspath(self.repo_path)),
            "branch": "unknown",
            "commit_sha": None,
            "full_sha": None,
            "author": None,
            "author_email": None,
            "intent": {
                "message": None,
                "timestamp": None
            },
            "stats": {
                "total_commits": -1,
                "is_first_commit": False
            }
        }
        if self.is_temporary:
            metadata["remote_url"] = self.source_repo_input
            if self.repo_slug:
                metadata["repository_slug"] = self.repo_slug

        try:
            # Safely get branch name
            try:
                metadata["branch"] = self.repo.active_branch.name
            except TypeError:
                metadata["branch"] = "detached"

            head = self.repo.head.commit
            metadata["commit_sha"] = head.hexsha[:8]
            metadata["full_sha"] = head.hexsha
            metadata["author"] = head.author.name
            metadata["author_email"] = head.author.email
            metadata["intent"]["message"] = head.message.strip()
            metadata["intent"]["timestamp"] = datetime.datetime.fromtimestamp(
                head.committed_date
            ).isoformat()
            metadata["stats"]["total_commits"] = self._get_commit_count()
            metadata["stats"]["is_first_commit"] = len(head.parents) == 0
        except Exception as e:
            metadata["error"] = str(e)

        return metadata

    @staticmethod
    def _extract_repo_slug(repo_url: str) -> Optional[str]:
        text = str(repo_url or "").strip()
        if not text:
            return None
        m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)(?:\.git)?/?$", text)
        if not m:
            return None
        owner = m.group(1).strip()
        repo = m.group(2).strip()
        if not owner or not repo:
            return None
        return f"{owner}/{repo}"

    def _get_commit_count(self) -> int:
        """Get total number of commits in the repository."""
        try:
            return len(list(self.repo.iter_commits()))
        except Exception:
            return -1

    def get_changed_files(self, compare_with: str = "HEAD~1") -> List[Dict[str, str]]:
        """
        Determines which files are ADDED, MODIFIED, or DELETED.
        Handles first commit gracefully by listing all files as ADDED.

        Args:
            compare_with: Git ref to compare against (default: HEAD~1)

        Returns:
            List of dictionaries with 'path' and 'change_type' keys
        """
        changes = []

        try:
            head_commit = self.repo.head.commit
        except Exception as e:
            return [{
                "path": "ERROR",
                "change_type": "ERROR",
                "error": str(e)
            }]

        # Handle first commit (no parents)
        if not head_commit.parents:
            for item in head_commit.tree.traverse():
                if item.type == 'blob':
                    changes.append({
                        "path": item.path,
                        "change_type": "ADDED",
                        "is_first_commit": True
                    })
            return changes

        # Compare against previous commit
        diffs = None
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                diffs = head_commit.diff(compare_with)
                last_error = None
                break
            except GitCommandError as e:
                last_error = e
                if attempt < 3:
                    time.sleep(0.8 * attempt)
        if diffs is None:
            # Fallback: compare against the first parent
            try:
                diffs = head_commit.diff(head_commit.parents[0])
            except Exception:
                return [{
                    "path": "ERROR",
                    "change_type": "ERROR",
                    "error": f"Failed to compute diff: {last_error}"
                }]

        for diff in diffs:
            change_type = "MODIFIED"
            rename_info = None

            if diff.new_file:
                change_type = "ADDED"
            elif diff.deleted_file:
                change_type = "DELETED"
            elif diff.renamed_file:
                change_type = "RENAMED"
                rename_info = {
                    "from": diff.a_path,
                    "to": diff.b_path
                }

            # Use b_path for new/modified, a_path for deleted
            path = diff.b_path if diff.b_path else diff.a_path

            change_entry = {
                "path": path,
                "change_type": change_type
            }

            if rename_info:
                change_entry["rename_info"] = rename_info

            changes.append(change_entry)

        return changes

    def list_all_files(self) -> List[Dict[str, str]]:
        """List all tracked files at HEAD as ADDED entries.

        Useful for first-run bootstrap where we want to analyze the
        entire repository rather than only diffs.

        Returns:
            List of dictionaries with 'path' and 'change_type' keys
        """
        files: List[Dict[str, str]] = []
        try:
            head_commit = self.repo.head.commit
            for item in head_commit.tree.traverse():
                if item.type == 'blob':
                    files.append({
                        "path": item.path,
                        "change_type": "ADDED",
                        "is_bootstrap": True
                    })
        except Exception as e:
            files.append({
                "path": "ERROR",
                "change_type": "ERROR",
                "error": str(e)
            })
        return files

    def get_file_content(self, file_path: str, ref: str = "HEAD") -> Optional[str]:
        """
        Safely retrieve file content from a specific git reference,
        with a filesystem fallback if the file is untracked or inside a submodule.

        Args:
            file_path: Path to file within repository
            ref: Git reference (default: HEAD)

        Returns:
            File content as string, empty string if missing, or None for binary/decode issues
        """
        # 1. Attempt Git retrieval
        try:
            return self.repo.git.show(f"{ref}:{file_path}")
        except GitCommandError:
            # File might not be tracked or commit might not exist at this ref
            pass
        except UnicodeDecodeError:
            # Binary file content that cannot be decoded safely
            return None
        except Exception:
            pass

        # 2. Filesystem fallback
        local_path = os.path.join(self.repo_path, file_path)
        if os.path.isfile(local_path):
            try:
                with open(local_path, "rb") as f:
                    content_bytes = f.read()
                try:
                    return content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    return None
            except Exception:
                return ""

        return ""

    def get_diff_content(self, file_path: str) -> Optional[str]:
        """
        Get the unified diff for a specific file.

        Args:
            file_path: Path to file within repository

        Returns:
            Diff content as string or None
        """
        try:
            head = self.repo.head.commit
            if head.parents:
                return self.repo.git.diff(
                    "HEAD~1", "HEAD", "--", file_path
                )
            else:
                # First commit - show full content
                return self.repo.git.show(f"HEAD:{file_path}")
        except Exception:
            return None

    def get_commit_history(self, max_count: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent commit history.

        Args:
            max_count: Maximum number of commits to retrieve

        Returns:
            List of commit information dictionaries
        """
        commits = []
        try:
            for commit in self.repo.iter_commits(max_count=max_count):
                commits.append({
                    "sha": commit.hexsha[:8],
                    "message": commit.message.strip().split('\n')[0],  # First line
                    "author": commit.author.name,
                    "date": datetime.datetime.fromtimestamp(
                        commit.committed_date
                    ).isoformat()
                })
        except Exception:
            pass

        return commits

    def has_uncommitted_changes(self) -> bool:
        """Check if there are uncommitted changes in the working directory."""
        try:
            return self.repo.is_dirty(untracked_files=True)
        except Exception:
            return False

    def cleanup(self):
        """Clean up temporary directory if repository was cloned."""
        if self.is_temporary and self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                print(f"Cleaned up temporary directory: {self.temp_dir}")
            except Exception as e:
                print(f"Warning: Failed to clean up temporary directory: {e}")

    def __del__(self):
        """Destructor to ensure cleanup on object deletion."""
        self.cleanup()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.cleanup()
