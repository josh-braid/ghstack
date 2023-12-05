#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import aiohttp
from typing import Any, Dict

import ghstack.circleci
import ghstack.github
import ghstack.github_utils

# Constants
CIRCLECI_URL_PATTERN = r"^https://circleci.com/gh/pytorch/pytorch/([0-9]+)"
SCCACHE_MARKER = "=================== sccache compilation log ==================="
CI_CONTEXT = "ci/circleci: pytorch_linux_xenial_py3_clang5_asan_test"

re_circleci_url = re.compile(CIRCLECI_URL_PATTERN)


def strip_sccache(log_content: str) -> str:
    """
    Strip sccache compilation log from the log content.
    """
    marker_pos = log_content.rfind(SCCACHE_MARKER)
    newline_before_marker_pos = log_content.rfind("\n", 0, marker_pos)
    return log_content[:newline_before_marker_pos]


async def fetch_and_process_log(circleci: ghstack.circleci.CircleCIEndpoint, build_id: str, params: Dict[str, Any]) -> str:
    """
    Fetch and process the log for a given build ID.
    """
    try:
        response = await circleci.get(f"project/github/{params['name']}/{params['owner']}/{build_id}")
        if not response["failed"]:
            return "❔"
        async with aiohttp.request("GET", response["steps"][-1]["actions"][-1]["output_url"]) as resp:
            log_json = await resp.json()
            log_messages = [entry["message"] for entry in log_json]
            return "\n" + strip_sccache("\n".join(log_messages))[-1500:]
    except Exception as e:
        return f"Error fetching log: {e}"


async def process_commit_node(node: Dict[str, Any], circleci: ghstack.circleci.CircleCIEndpoint, params: Dict[str, Any]) -> str:
    """
    Process a single commit node and return formatted output.
    """
    commit = node["commit"]
    status = commit.get("status")
    if not status:
        return f"❔ {commit['oid'][:8]} {commit['messageHeadline']}"

    for context in status["contexts"]:
        if context["context"] != CI_CONTEXT:
            continue
        match = re_circleci_url.match(context["targetUrl"])
        if not match:
            return f"🍆 {commit['oid'][:8]} {commit['messageHeadline']}"

        build_id = match.group(1)
        log_text = await fetch_and_process_log(circleci, build_id, params)
        state_icon = "✅" if context["state"] == "SUCCESS" else "❌"
        return f"{state_icon} {commit['oid'][:8]} {commit['messageHeadline']} ({build_id}){log_text}"

    return "❔ Unknown Status"


async def main(pull_request: str, github: ghstack.github.GitHubEndpoint, circleci: ghstack.circleci.CircleCIEndpoint) -> None:
    params = ghstack.github_utils.parse_pull_request(pull_request)
    query = """
        query ($name: String!, $owner: String!, $number: Int!) {
            repository(name: $name, owner: $owner) {
                pullRequest(number: $number) {
                    commits(last: 100) {
                        nodes {
                            commit {
                                oid
                                messageHeadline
                                status {
                                    contexts {
                                        context
                                        state
                                        targetUrl
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    """
    try:
        response = await github.graphql(query, **params)
        nodes = response["data"]["repository"]["pullRequest"]["commits"]["nodes"]
        results = await asyncio.gather(*(process_commit_node(n, circleci, params) for n in nodes))
        for result in results:
            print(result)
    except Exception as e:
        print(f"Error processing pull request: {e}")


# The main entry point for the script
if __name__ == "__main__":
    # Initialize GitHub and CircleCI endpoints here and call main()
    pass
