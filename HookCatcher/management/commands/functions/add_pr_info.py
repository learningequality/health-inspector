import json
import sys
from collections import defaultdict

import requests
from django.conf import settings  # database dir
from HookCatcher.models import PR, Commit, State

STATES_FOLDER = settings.STATES_FOLDER  # folder within git repo that organizes the list of states

# header of the git GET request
GIT_HEADER = {
    'Authorization': 'token ' + settings.GIT_OAUTH,
}

# GIT_REPO_API example form "https://api.github.com/repos/MingDai/kolibri"
GIT_REPO_API = 'https://api.github.com/repos/{0}'.format(settings.GIT_REPO)


# Read a single json file that represents a state and save into models
# save the commit object into the database
def parseStateJSON(stateRepresentation, gitCommitObj):
    # get the raw json file for each state
    rawURL = stateRepresentation["download_url"]
    reqRawState = requests.get(rawURL, headers=GIT_HEADER)
    try:
        # save the json as a regular string
        rawState = json.loads(reqRawState.text)
        s = State(stateName=rawState['name'],
                  stateDesc=rawState['description'],
                  stateUrl=rawState['url'],
                  gitCommit=gitCommitObj)
        s.save()
        print('Adding State: {0}'.format(s))
        return s

    except ValueError:
        print('There are improperly formatted json files within the folder "{0}"'.format(STATES_FOLDER))  # noqa: E501
        sys.exit(0)
    return None


# Pass in the information about the PR
# Access the state JSON files and saving data into models
def saveStates(gitCommitObj):
    # initialize list of state obj
    stateObjList = []

    # get the directory of the states folder with the JSON states
    statesDir = '{0}?ref={1}'.format(STATES_FOLDER, gitCommitObj.gitHash)
    # example url https://api.github.com/repos/MingDai/kolibri/contents/states?ref=b5f089b
    gitContentURL = '{0}/contents/{1}'.format(GIT_REPO_API, statesDir)

    reqStatesList = requests.get(gitContentURL, headers=GIT_HEADER)

    if (reqStatesList.status_code == 200):
        gitStatesList = json.loads(reqStatesList.text)
        # if stateList = 0, then exit as well because there are no states to add
        # if the states of this commit has already been added to database then don't add it again

        # filter gitHash first
        if(State.objects.filter(gitCommit=gitCommitObj).count() < len(gitStatesList)):
            # save the json content for each file of the STATES_FOLDER defined in user_settings
            for eachState in gitStatesList:
                stateObjList.append(parseStateJSON(eachState,
                                                   gitCommitObj))
        else:  # check for repeated commits make sure funciton is idempotent
            print('States of commit "{0}" have already been added'.format(gitCommitObj.gitHash[:7]))  # noqa: E501
    else:
        print('Folder of states "{0}" was not found in branch "{1}" commit "{2}"'.format(STATES_FOLDER,              # noqa: E501
                                                                                         gitCommitObj.gitBranch,     # noqa: E501
                                                                                         gitCommitObj.gitHash[:7]))  # noqa: E501
        sys.exit(0)
    return stateObjList


# get a Commit Object using a Commit SHA from database
def saveCommit(gitRepoName, gitBranchName, gitCommitSHA):
    # check if this commit is already in database
    if(Commit.objects.filter(gitRepo=gitRepoName,
                             gitBranch=gitBranchName,
                             gitHash=gitCommitSHA).count() <= 0):
        commitObj = Commit(gitRepo=gitRepoName, gitBranch=gitBranchName, gitHash=gitCommitSHA)
        commitObj.save()
        print('Adding states of new commit "{0}"'.format(gitCommitSHA[:7]))
        return commitObj
    else:
        return Commit.objects.get(gitRepo=gitRepoName,
                                  gitBranch=gitBranchName,
                                  gitHash=gitCommitSHA)


# arguments can either be: int(prNumber) or dict(payload)
def add_pr_info(prnumber_payload):
    # function called: addPRinfo(prNumber)
    if isinstance(prnumber_payload, int):
        prNumber = prnumber_payload
        # get the information about a certain PR through Github API using PR number
        gitPullURL = '{0}/pulls/{1}'.format(GIT_REPO_API, prNumber)
        reqSpecificPR = requests.get(gitPullURL, headers=GIT_HEADER)
        # make sure connection to Github API was successful
        if (reqSpecificPR.status_code == 200):
            print('Accessing "{0}"'.format(gitPullURL))
            specificPR = json.loads(reqSpecificPR.text)
        else:
            print('Could not retrieve PR {0} from your git Repository'.format(prNumber))
            sys.exit(0)
    elif isinstance(prnumber_payload, dict):
        specificPR = prnumber_payload
        prNumber = specificPR['number']
    else:
        # exit out of the whole function
        print('Invalid payload')
        sys.exit(0)

    # Base of Pull Request save branch name and the commitSHA
    baseRepoName = specificPR['base']['repo']['full_name']
    baseBranchName = specificPR['base']['ref']
    baseCommitObj = saveCommit(baseRepoName, baseBranchName, specificPR['base']['sha'])

    '''
    NOTE: this will add a row to the Commit table even if there are no states
    that are asssociated with the commit, storing unassociated Commit objects.
    Same with the base of the PR

    '''

    # head of the Pull Request save branch name and commitSHA
    headRepoName = specificPR['head']['repo']['full_name']
    headBranchName = specificPR['head']['ref']
    headCommitObj = saveCommit(headRepoName, headBranchName, specificPR['head']['sha'])

    saveStates(baseCommitObj)
    saveStates(headCommitObj)

    # returns a dictionary of states that were added {'stateName1': (baseVers, headVers), ...}
    # this list is to be sent to screenshot generator to be taken screenshot of
    newStatesDict = defaultdict(list)
    baseStatesList = State.objects.filter(gitCommit=baseCommitObj)
    # save the json state representations into the database and add added states to list
    headStatesList = State.objects.filter(gitCommit=headCommitObj)

    # key is url because devs can make mistake of changing state name
    # if url changes betweeen state versions then need way of shared identifier
    for stateObjB in baseStatesList:
        newStatesDict[stateObjB.stateName].append(stateObjB)
    for stateObjH in headStatesList:
        # {'key' : baseStateObj, headStateObj, 'key': ...}
        newStatesDict[stateObjH.stateName].append(stateObjH)

    # Add merged pr commit state into states table
    # GITHUB API HAS NO MERGED_COMMIT_SHA WHEN FIRST OPENED
    prCommitObj = None
    # check if there is a hash for the merged commit of a pr
    if(specificPR['merge_commit_sha']):
        prCommitObj = saveCommit(baseRepoName, baseBranchName, specificPR['merge_commit_sha'])
        # Merged PR commit use the repo that the PR will end up in (base)
        # not sure how to take screenshot of this so not included in saveStates List
        saveStates(prCommitObj)

    gitPRNumber = specificPR['number']
    # Update an entry when the merged pr commit hash now exists on Git
    # PR already exists in database
    if(PR.objects.filter(gitPRNumber=gitPRNumber).count() > 0):
        prObject = PR.objects.get(gitPRNumber=gitPRNumber)  # get existing pr entry
        # check if the pr commit is different from the previous
        if (prObject.gitPRCommit != prCommitObj):
            prObject.gitPRCommit = prCommitObj
            prObject.save()
            print("Successfully updated PR {0}".format(gitPRNumber))

    else:  # there is no merge_commit_sha for a newly opened PR
        # save information into the PR model
        prObject = PR(gitRepo=baseRepoName,
                      gitPRNumber=gitPRNumber,
                      gitTargetCommit=baseCommitObj,
                      gitSourceCommit=headCommitObj,
                      gitPRCommit=prCommitObj)  # gitPRCommit == None
        prObject.save()
        print("Successfully added PR {0}".format(gitPRNumber))

    return newStatesDict  # {'statename': (headObj, baseObj), 'statename1'...}
