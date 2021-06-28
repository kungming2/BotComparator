# BotComparator

This is a script that compares various moderator bots on Reddit and looks at their overall number of subreddits moderated, subscribers, moderators, etc. Findings are regularly posted to r/botwatch. 

#### Configuration (Bot List)

The script can read the list of bots to check from one of two places:

1. A [hosted list of usernames](https://www.reddit.com/r/translatorBOT/wiki/moderator_bots) kept by u/kungming2 on r/translatorBOT.
2. A user-provided list in the `Data` folder.

Either way, the data should be formatted in YAML as a dictionary with lists. A mirrored local copy of the hosted list in included in this repository for reference. The list allows for multiple accounts of a bot to be included together as one entry.

#### Usage

The bot caches bot data and subreddits' moderator lists in order to speed up successive runs of the bot, especially when a bot account has not been added to any new subreddits. An initial full run will cache information and make successive runs that use the cache much faster. It's recommended to use the cache unless you need some information that may have changed between the time of caching and running (e.g. exact number of subscribers). 

* A **quick run** just quickly gets the number of subreddits moderated by a bot.
* A **full run** gets and returns all the information, including subscribers, moderators, etc. 
