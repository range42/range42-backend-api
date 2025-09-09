#!/bin/bash

# create venv 
python3 -m venv .venv

# enable venv
source .venv/bin/activate

# setup depends 
pip install --upgrade pip
pip install -r requirements.txt


# setup ansible dependencies : 
ansible-galaxy collection install -r requirements.yml -p ./collections 

