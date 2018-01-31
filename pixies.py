#!/usr/bin/env python

'''
Copyright [2017] [Jon Robson]

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

See the License for the specific language governing permissions and
limitations under the License.
'''
import json
import os
import subprocess
import select
import time
import datetime
import signal
import argparse

DRY_RUN = False
busy = False
HOST_NAME = 'gerrit.wikimedia.org'
ORIGIN = 'gerrit' #make origin

def get_parser():
    helper = {
        'project': 'A valid project name e.g. `mediawiki/extensions/Popups`',
    }
    parser = argparse.ArgumentParser()
    for key, msg in helper.items():
        parser.add_argument('--%s'%key, help=msg)
    return parser

def log(msg):
    print msg
    f = open('pixies.log', 'a')
    f.write('[%s] %s\n'%(datetime.datetime.utcnow(), msg)) 
    f.close()

def getCommit():
    process = subprocess.Popen('git rev-parse HEAD', stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, error = process.communicate()
    return output.strip()

def submitReview( score, msg = None ):
    if DRY_RUN:
        log("Operating in dry run mode. Message not sent.")
        return
    args = ['ssh', '-p 29418',
        HOST_NAME, 'gerrit', 'review',
        '--code-review', score ]
    if msg is None:
        if score == "-1":
            msg = "\I cannot rebase this manually."
        else:
            msg = "\I have rebased your patch for you against current master."

    args.extend( [ '--message', '"%s"'%msg ] )
    args.append( getCommit() )
    subprocess.Popen( args ).communicate()

def runCommand(cmd):
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, error = process.communicate()
    log("run %s"%cmd)
    return output, error

def rebaser(gerritId, parent):
    log("rebasing %s for %s"%(gerritId, parent))
    runCommand("pwd")
    runCommand("git fetch %s"%ORIGIN)
    runCommand("git checkout  %s/master"%ORIGIN)
    msg, err = runCommand("git branch -r --contains %s"%parent)

    runCommand("git review -d %s"%gerritId)
    npmInstall, err = runCommand("git diff HEAD^ package.json")
    if npmInstall:
        log("package.json update detected. Running `npm install`.")
        runCommand("npm install")

    if not msg:
        submitReview("0", "This patch has dependencies. Please ask me again when it doesn't.")
    else:
        submitReview("0", "message received master!")
        runCommand("git branch -D rebaser-tmp")
        runCommand("git checkout -b rebaser-tmp")
        runCommand("git rebase %s/master"%ORIGIN)
        runCommand("npm run build")
        dirty, err = runCommand("git diff | grep \"<<<<\"")

        if not dirty:
            log("Diff is not dirty")
            runCommand("git add -u")
            runCommand("git rebase --continue")
            runCommand("git review")
            submitReview("0")
        else:
            log("Diff is dirty")
            runCommand("git rebase --abort")
            submitReview("-1")

def processEvent(event):
    busy = True
    if "change" in event:
        gerritId = event["change"]["number"]
        log("Processing change %s"%gerritId)
        plusTwo = False

        if "comment" in event and event["comment"].endswith("\nrebase") or plusTwo:
            if "patchSet" in event and "parents" in event["patchSet"]:
                parents = event["patchSet"]["parents"]
                if len(parents) == 1:
                    parentCommit = parents[0]
                    rebaser(gerritId, parentCommit)
                else:
                    log("Several parents, cannot rebase: %s"%parents)
        else:
            log("Ignoring")
    else:
        log("Unknown event. Cannot process.")
    log("accepting new jobs")
    busy = False

# 1 tick = 1s
TICK = 1
# every 4 hrs reboot
RESTART_TICKS = ( 60 * 60 ) * 4

def watch(project):
    log("Watching")
    ticks = 0
    args = ['ssh', '-p 29418', HOST_NAME, 'gerrit', 'stream-events' ]
    process = subprocess.Popen( args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid )
    p = select.poll()
    p.register(process.stdout)

    while True:
        if busy:
            log("busy")
        elif p.poll(1):
            line = process.stdout.readline()
            try:
                event = json.loads(line)
                if "project" in event and event["project"] == project:
                    log("Process event")
                    processEvent(event)
            except ValueError:
                log("Cannot read" + line)
                pass
        log("sleeping")
        ticks += 1
        if ticks > RESTART_TICKS:
            log("refreshing ssh connection")
            os.killpg( os.getpgid( process.pid ), signal.SIGTERM )
            process = subprocess.Popen( args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid )
            p = select.poll()
            p.register(process.stdout)
            ticks = 0
            log("refreshed ssh connection")
        time.sleep(TICK)
    
if __name__ == '__main__':
    parser = get_parser()
    args = parser.parse_args()
    try:
        project = args.project
        runCommand("npm install")
        watch(project)
    except KeyError:
        log("ERROR: Please define a project")
