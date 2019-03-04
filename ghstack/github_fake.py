import asyncio
import graphql
import json
import traceback

# Oof! Python 3.7 only!!
from dataclasses import dataclass

from typing import Dict, NewType, List, Optional, Any
# TODO: do something better about this...
try:
    from mypy_extensions import TypedDict
except ImportError:
    # Avoid the dependency on the mypy_extensions package.
    # It is required, however, for type checking.
    def TypedDict(name, attrs, total=True):  # type: ignore
        return Dict[Any, Any]

GraphQLId = NewType('GraphQLId', str)
GitHubNumber = NewType('GitHubNumber', int)

UpdatePullRequestInput = TypedDict('UpdatePullRequestInput', {
    'baseRefName': Optional[str],
    'title': Optional[str],
    'body': Optional[str],

    'clientMutationId': Optional[str],
    'pullRequestId': GraphQLId,
})

CreatePullRequestInput = TypedDict('CreatePullRequestInput', {
    'baseRefName': str,
    'headRefName': str,
    'title': str,
    'body': str,

    'clientMutationId': Optional[str],
    'ownerId': GraphQLId,
})

# mypy doesn't like these... figure out how to properly forward declare
class Repository:
    ...

class PullRequest:
    ...

class Root:
    ...

# The "database" for our mock instance
class GitHubState:
    repositories: Dict[GraphQLId, Repository]
    pull_requests: Dict[GraphQLId, PullRequest]
    _next_id: int
    _next_pull_request_number: Dict[GraphQLId, int]
    root: Root

    def next_id(self) -> GraphQLId:
        r = GraphQLId(str(self._next_id))
        self._next_id += 1
        return r

    def next_pull_request_number(self, repo_id: GraphQLId) -> GitHubNumber:
        r = GitHubNumber(self._next_pull_request_number[repo_id])
        self._next_pull_request_number[repo_id] += 1
        return r

    def __init__(self) -> None:
        self.repositories = {}
        self.pull_requests = {}
        self._next_id = 5000
        self._next_pull_request_number = {}
        self.root = Root()

        # Populate it with the most important repo ;)
        self.repositories[GraphQLId("1000")] = Repository(
            id=GraphQLId("1000"),
            name="pytorch",
            nameWithOwner="pytorch/pytorch",
        )
        self._next_pull_request_number[GraphQLId("1000")] = 500

@dataclass
class Node:
    id: GraphQLId

@dataclass
class PullRequestConnection:
    nodes: List[PullRequest]

@dataclass
class Repository(Node):
    name: str
    nameWithOwner: str

    def pullRequest(self, info: graphql.GraphQLResolveInfo, number: GitHubNumber) -> PullRequest:
        for pr in info.context.pull_requests.values():
            if self == pr.repository(info) and pr.number == number:
                return pr
        raise RuntimeError(
            "unrecognized pull request #{} in repository {}"
            .format(number, self.nameWithOwner))

    def pullRequests(self, info: graphql.GraphQLResolveInfo) -> PullRequestConnection:
        return PullRequestConnection(
                nodes=list(filter(lambda pr: self == pr.repository(info), info.context.pull_requests.values())))

@dataclass
class PullRequest(Node):
    # baseRef: Optional[Ref]
    baseRefName: str
    body: str
    # closed: bool
    # headRef: Optional[Ref]
    headRefName: str
    # headRepository: Optional[Repository]
    # maintainerCanModify: bool
    number: GitHubNumber
    _repository: GraphQLId  # cycle breaker
    # state: PullRequestState
    title: str
    url: str

    def repository(self, info: graphql.GraphQLResolveInfo) -> Repository:
        return info.context.repositories[self._repository]

@dataclass
class UpdatePullRequestPayload:
    clientMutationId: Optional[str]
    pullRequest: PullRequest

@dataclass
class CreatePullRequestPayload:
    clientMutationId: Optional[str]
    pullRequest: PullRequest

class Root:
    def repository(self, info: graphql.GraphQLResolveInfo, owner: str, name: str) -> Repository:
        nameWithOwner = "{}/{}".format(owner, name)
        for r in info.context.repositories.values():
            if r.nameWithOwner == nameWithOwner:
                return r
        raise RuntimeError("unknown repository {}".format(nameWithOwner))

    def node(self, info: graphql.GraphQLResolveInfo, id: GraphQLId) -> Node:
        if id in info.context.repositories:
            return info.context.repositories[id]
        elif id in info.context.pull_requests:
            return info.context.pull_requests[id]
        else:
            raise RuntimeError("unknown id {}".format(id))

    def updatePullRequest(self,
                          info: graphql.GraphQLResolveInfo,
                          input: UpdatePullRequestInput
                          ) -> UpdatePullRequestPayload:
        pr = info.context.pull_requests[input['pullRequestId']]
        # If I say input.get('title') is not None, mypy
        # is unable to infer input['title'] is not None
        if 'title' in input and input['title'] is not None:
            pr.title = input['title']
        if 'baseRefName' in input and input['baseRefName'] is not None:
            pr.baseRefName = input['baseRefName']
        if 'body' in input and input['body'] is not None:
            pr.body = input['body']
        return UpdatePullRequestPayload(
                clientMutationId=input.get('clientMutationId'),
                pullRequest=pr)

    def createPullRequest(self,
                          info: graphql.GraphQLResolveInfo,
                          input: CreatePullRequestInput
                          ) -> CreatePullRequestPayload:
        id = info.context.next_id()
        repo = info.context.repositories[input['ownerId']]
        number = info.context.next_pull_request_number(input['ownerId'])
        pr = PullRequest(
            id=id,
            _repository=input['ownerId'],
            number=number,
            url="https://github.com/{}/pull/{}".format(repo.nameWithOwner, number),
            baseRefName=input['baseRefName'],
            headRefName=input['headRefName'],
            title=input['title'],
            body=input['body'],
        )
        info.context.pull_requests[id] = pr
        return CreatePullRequestPayload(
                clientMutationId=input.get('clientMutationId'),
                pullRequest=pr)

with open('github-fake/src/schema.graphql') as f:
    GITHUB_SCHEMA = graphql.build_schema(f.read())

# Ummm.  I thought there would be a way to stick these on the objects
# themselves (in the same way resolvers can be put on resolvers) but
# after a quick read of default_resolve_type_fn it doesn't look like
# we ever actually look to value for type of information.  This is
# pretty clunky lol.
GITHUB_SCHEMA.get_type('Repository').is_type_of = lambda obj, info: isinstance(obj, Repository)
GITHUB_SCHEMA.get_type('PullRequest').is_type_of = lambda obj, info: isinstance(obj, PullRequest)

class FakeGitHubGraphQLEndpoint(object):
    def __init__(self):
        self.context = GitHubState()
        self.future = True

    def graphql(self, query: str, **kwargs: Any) -> Any:
        r = graphql.graphql_sync(
                schema=GITHUB_SCHEMA,
                source=query,
                root_value=self.context.root,
                context_value=self.context,
                variable_values=kwargs)
        if r.errors:
            # The GraphQL implementation loses all the stack traces!!!
            # D:  You can 'recover' them by deleting the
            # 'except Exception as error' from GraphQL-core-next; need
            # to file a bug report
            raise RuntimeError("GraphQL query failed with errors:\n\n{}"
                               .format("\n".join(str(e) for e in r.errors)))
        # The top-level object isn't indexable by strings, but
        # everything underneath is, oddly enough
        return {'data': r.data}