# Lighthouse
Service to auto-post to social media when a streamer goes live. Built with Docker/Python/Selenium

## Requirements
* Linux machine with docker and docker-compose installed
* Probably at least 4GB of RAM for selenium to stretch its legs

## Setup
* git clone this repo
* fill in .env with your URLs and secrets
* execute either `scripts/run.sh` or `docker-compose up -d` to start

## Known bugs

* Buggy checks, sometimes returning false positives. Todo: Implement more strict checks or use smoothing 
* Once it posted to Reddit endlessly. Sometimes Reddit lies and says it didn't post something with error 504. Todo: Need to catch. 
