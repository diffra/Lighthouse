#!/usr/bin/python
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from twitter import *
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
import datetime
import facebook
import json
import os
import praw
import redis
import requests
import sched
import sys
import time

print("Starting up...")

r = redis.Redis(host='redis', decode_responses=True)

#load the configs into variables
envkeys=[
  'checkInterval', 'streamerName', 'youTubeUrl', 'twitchUrl', 'instaUrl', 'fbGroup', 'redditSub',                          #Global configs
  'redditClientId', 'redditClientSecret','redditUsername', 'redditPassword',                                #Reddit API credentials
  'instaUsername', 'instaPassword',                                                                         #Instagram logins (needed to check instagram live status)
  'twitterApiKey','twitterSecretKey','twitterBearerToken','twitterAccessToken','twitterAccessTokenSecret',  #Twitter API credentials
  'facebookUsername', 'facebookPassword'                                                                    #Facebook credentials for posting
  ]
for envkey in envkeys:
  vars()[envkey] = os.environ[envkey]


#Enumeration of status indicators:
#
# 0 - offline
# 1 - online but alert not sent
# 2 - online, alert sent
#


#Setup chrome options once and apply repeatedly to avoid possible memory leak
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument('--disable-extensions')
chrome_options.add_argument('--headless')
chrome_options.add_argument('--disable-gpu')
chrome_options.add_argument('--no-sandbox')

firefox_options = webdriver.FirefoxOptions()
firefox_options.add_argument("-headless")


##########
#
# Utility functions
#
# Data validation, redis sync, etc.
#
##########

#helper functions in lieu of building a real object
def getvalue(r, key):
  return r.hget('LiveStatus', key)
def setvalue(r, key, value):
  r.hset('LiveStatus', key, int(value))

def maybeUpdateRedis(r, source, newstatus):
  oldstatus = getvalue(r, source)
  #print("MaybeUpdateRedis: o:" + str(oldstatus) + " n:" + str(newstatus))
  if (int(oldstatus) != int(newstatus)):
    #don't update status to 1 if it's already 2, only back to zero.
    if (newstatus == 0 or (int(newstatus) == int(1) and int(oldstatus) == int(0))):
      #print("Setting value")
      setvalue(r, source, newstatus)

def getStreamUrl(source):
  global youTubeUrl
  global twitchUrl
  global instaUrl
  
  if (source=='YouTube'):
    return youTubeUrl
  if (source=='Twitch'):
    return twitchUrl
  if (source=='Insta'):
    return instaUrl
  else:
    return False

#TODO ideally redis should be saving this and maybe it is...
setvalue(r,'YouTube', int(0))
setvalue(r,'Twitch', int(0))
setvalue(r,'Insta', int(0))

###########
#
# Livestream check functions
# 
# Use Selenium to drive Chrome and open the page to look for tags indicating offline/online.
#
###########

def checkInsta(r):
  global instaUrl
  global instaUsername
  global instaPassword
  global firefox_options

  print("CIG: Starting up...")
  driver = webdriver.Remote(
      command_executor='http://hub:4444/wd/hub',
      options=firefox_options,
      desired_capabilities=DesiredCapabilities.FIREFOX
  )
  print("CIG: Loading home page")

  driver.get('https://www.instagram.com/accounts/login/?source=auth_switcher')  

  time.sleep(3)
  #print(driver.page_source.encode("utf-8"))
  user = driver.find_element_by_name('username')
  pasw = driver.find_element_by_name('password')

  user.send_keys(instaUsername)
  time.sleep(1)
  pasw.send_keys(instaPassword)
  time.sleep(3)
  pasw.send_keys(webdriver.common.keys.Keys.RETURN)
  print("CIG: Checking page")
  driver.get(instaUrl)
  time.sleep(3)
  #data-testid="live-badge"          "//span[@aria-label='LIVE']"
  if (driver.find_elements_by_xpath("//span[@data-testid='live-badge']")):
    #Live
    print("CIG: LIVE!")
    maybeUpdateRedis(r,'Insta', 1)
    driver.quit() 
  else:
    print("CIG: Not live :(")
    maybeUpdateRedis(r,'Insta', 0)
    driver.quit() 

def checkYouTube(r):
  print("CYT: Starting up...")
  global chrome_options
  driver = webdriver.Remote(
      command_executor='http://hub:4444/wd/hub',
      options=chrome_options,
      desired_capabilities=DesiredCapabilities.CHROME
  )
  
  driver.get(youTubeUrl)
  #Wait for DOM/JS to load before we check for the element 
  print("CYT:Waiting for DOM...")
  time.sleep(5)
  try:
    elements=driver.find_element_by_xpath("//span[@aria-label='LIVE']")
    #if we made it here we're live
    print("CYT:Live!")
    maybeUpdateRedis(r,'YouTube', 1)
  except:
    #assume not live 
    print("CYT:Not live :(")
    maybeUpdateRedis(r,'YouTube', 0)
  driver.quit() 

def checkTwitch(r): 
  print("CTW: Starting up...")
  global chrome_options
  driver = webdriver.Remote(
      command_executor='http://hub:4444/wd/hub',
      options=chrome_options,
      desired_capabilities=DesiredCapabilities.CHROME
  )
  
  driver.get(twitchUrl)
  #Wait for DOM/JS to load before we check for the element
  print("CTW:Waiting for DOM...")
  time.sleep(5)
  try:
    elements= driver.find_element_by_class_name("channel-status-info--offline")
    #if we made it here we're not live
    print("CTW:Not Live :(")
    maybeUpdateRedis(r,'Twitch', 0)
  except:
    #No offline box? guess we're live!
    #TODO: be more careful with what exception we catch here 
    print("CTW:Live!")
    maybeUpdateRedis(r,'Twitch', 1)
  driver.quit() 



##########
#
# Push functions
#
# Push to various platforms
#
##########
def pushUpdates(r):
  for source in ['Twitch', 'YouTube', 'Insta']:
    if (int(getvalue(r,source)) == int(1)):
      if(redditSub):
        pushUpdateToReddit(source)
      if(twitterAccessToken):
        pushUpdateToTwitter(source)
      if(fbGroup):
        pushUpdateToFacebook(source)
      print("PU: Finished pushing updates for " + source)
      #Set value directly once we push the update
      #TODO: check to see if update was successful first.
      setvalue(r,source,2)

def pushUpdateToReddit(source):
  print("PU: Updating Reddit...")
  reddit = praw.Reddit(
    client_id=redditClientId,
    client_secret=redditClientSecret,
    user_agent="python:Livestream notifier Bot::v1 (by u/oregontraildropout)",
    username=redditUsername,
    password=redditPassword,
  )
  streamUrl=getStreamUrl(source)
  subreddit = reddit.subreddit(redditSub) # Initialize the subreddit to a variable
  title = streamerName + 'is currently live on ' + source
  selftext = 'Tune in at: ' + streamUrl
  subreddit.submit(title,selftext=selftext)
  print("PU: Reddit updated")
  #TODO: add error handling

def pushUpdateToTwitter(source):
  print("PU: Updating Twitter...")
  t = Twitter(
    auth=OAuth(twitterAccessToken, twitterAccessTokenSecret, twitterApiKey, twitterSecretKey))
  streamUrl=getStreamUrl(source)
  title = streamerName + ' is currently live on ' + source + '. Check it out here: ' + streamUrl
  selftext = 'Tune in at: ' + streamUrl
  t.statuses.update(status="")
  print("PU: Twitter updated")
  #TODO: add error handling

def pushUpdateToFacebook(source):
  #First of all, screw facebook, okay.
  #They make screen scraping all but impossible.
  #/rant
  global firefox_options

  print("PFB: Starting up...")
  driver = webdriver.Remote(
      command_executor='http://hub:4444/wd/hub',
      options=firefox_options,
      desired_capabilities=DesiredCapabilities.FIREFOX
  )
  print("PFB: Loading home page")

  driver.get('https://www.facebook.com/')  

  time.sleep(3)
  
  #print(driver.page_source.encode("utf-8"))
  user = driver.find_element_by_name('email')
  pasw = driver.find_element_by_name('pass')

  user.send_keys(facebookUsername)
  time.sleep(1)
  pasw.send_keys(facebookPassword)
  time.sleep(3)
  #driver.save_screenshot("/app/screenshot1.png")
  action = webdriver.common.action_chains.ActionChains(driver)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.ENTER)
  action.perform()
  time.sleep(1)
  #driver.save_screenshot("/app/screenshot2.png")
  time.sleep(1)
  #driver.save_screenshot("/app/screenshot2.1.png")
  time.sleep(1)
  #driver.save_screenshot("/app/screenshot2.2.png")
  print("PFB: Loading group")
  driver.get(fbGroup)
  time.sleep(3)
  #driver.save_screenshot("/app/screenshot3.png")
  print("PFB: Creating post...")
  action = webdriver.common.action_chains.ActionChains(driver)
  action.move_by_offset(630, 307)
  action.click()
  action.perform()
  time.sleep(1)
  actions = webdriver.common.action_chains.ActionChains(driver)
  streamUrl=getStreamUrl(source)
  actions.send_keys(streamerName + ' is currently live on ' + source + '. Check it out here: ' + streamUrl)
  actions.perform()
  print("PFB: Taking screenshot...")
  #driver.save_screenshot("/app/screenshot4.png")
  time.sleep(1)
  action = webdriver.common.action_chains.ActionChains(driver)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.TAB)
  action.send_keys(webdriver.common.keys.Keys.ENTER)
  action.perform()
  time.sleep(1)
  #driver.save_screenshot("/app/screenshot4.png")
  driver.quit()   

########################
#
# Main Loop starts here
#
# Everything runs on scheduled tasks using APScheduler
#
########################

#Setup scheduler
s = BackgroundScheduler()
s.start()
if(twitchUrl):
  jobCheckTwitch = s.add_job(checkTwitch, 'interval', seconds=int(checkInterval), args=[r], id='checkTwitch', next_run_time=datetime.datetime.now()+datetime.timedelta(seconds = 70))
if(youTubeUrl):
  jobCheckYouTube = s.add_job(checkYouTube, 'interval', seconds=int(checkInterval), args=[r], id='checkYouTube', next_run_time=datetime.datetime.now()+datetime.timedelta(seconds = 40))
if(instaUrl):
  jobCheckInsta =  s.add_job(checkInsta, 'interval', seconds=int(checkInterval), args=[r], id='checkInsta', next_run_time=datetime.datetime.now()+datetime.timedelta(seconds = 20))

jobPushUpdates = s.add_job(pushUpdates, 'interval', seconds=10, args=[r], id='pushUpdates')


#Main loop begins
try:
    while True:
        #use this thread to post status periodically
        time.sleep(60)
        if(youTubeUrl):
          print('Youtube status: ' + getvalue(r, 'YouTube'))
        if(twitchUrl):
          print('Twitch status: ' + getvalue(r, 'Twitch'))
        if(instaUrl):
          print('Insta status: ' + getvalue(r, 'Insta'))
except KeyboardInterrupt:
    print('interrupted!')
    s.shutdown()
