import json
import logging
import os
import pickle
import pprint
import sys
import time
from types import SimpleNamespace

import praw
import prawcore
import yaml


"""DEFINING VARIABLES"""

SOURCE_FOLDER = os.path.dirname(os.path.realpath(__file__))
FILE_PATHS = {
    "auth": "/_settings.yaml",
    "bot_list": "/Data/_bots.yaml",
    "error": "/Data/_error.md",
    "output": "/Data/_output.json",
    "logs": "/Data/_logs.md",
    "pickled": "/Data/_pickle_data.dat",
}
for file_type in FILE_PATHS:
    FILE_PATHS[file_type] = SOURCE_FOLDER + FILE_PATHS[file_type]
FILE_ADDRESS = SimpleNamespace(**FILE_PATHS)
REDDIT = None
AUTH = None
pp = pprint.PrettyPrinter(indent=4)


"""LOGGER SETUP"""

# Set up the logger. By default only display INFO or higher levels.
log_format = "%(levelname)s: %(asctime)s - [BotComparator] %(message)s"
logging.basicConfig(format=log_format, level=logging.INFO)

# Set the logging time to UTC.
logging.Formatter.converter = time.gmtime
logger = logging.getLogger(__name__)

# Define the logging handler (the file to write to.)
# By default only log INFO level messages or higher.
handler = logging.FileHandler(FILE_ADDRESS.logs, "a", "utf-8")
handler.setLevel(logging.INFO)

# Set the time format in the logging handler.
d = "%Y-%m-%dT%H:%M:%SZ"
handler.setFormatter(logging.Formatter(log_format, datefmt=d))
logger.addHandler(handler)


"""STARTUP FUNCTIONS"""


def load_information(file_address):
    """Function that takes information on login/OAuth access from an
    external YAML file and loads it as a dictionary. It also loads the
    settings as a dictionary. Both are returned in a tuple.

    :return: A tuple containing two dictionaries, one for authentication
             data and the other with settings.
    """
    with open(file_address, "r", encoding="utf-8") as f:
        loaded_data = yaml.safe_load(f.read())

    return loaded_data


def load_pickled():
    """A simple function to load saved data to speed up operations."""

    try:
        infile = open(FILE_ADDRESS.pickled, "rb")
    except FileNotFoundError:
        return {}
    else:
        file_data = pickle.load(infile)
        infile.close()

    return file_data


def login():
    """A simple function to log in and authenticate to Reddit."""
    global REDDIT
    global AUTH

    AUTH = SimpleNamespace(**load_information(FILE_ADDRESS.auth))

    # Authenticate the main connection.
    REDDIT = praw.Reddit(
        client_id=AUTH.app_id,
        client_secret=AUTH.app_secret,
        password=AUTH.password,
        user_agent=AUTH.user_agent,
        username=AUTH.username,
    )
    logger.info(f"Startup: Activating {AUTH.user_agent}.")
    logger.info("Startup: Logging in as u/{}.".format(AUTH.username))

    return


"""COMPARATOR FUNCTIONS"""


def get_subreddit_public_moderated(username_list, quick_run=False):
    """A function that retrieves (via the web)
    a list of public subreddits that a user moderates.
    Note that this function actively removes user subreddits
    prefixed with "u_" from the list of moderated subs.

    :param username: List of users.
    :return: A list of subreddits that the users moderate.
    """
    subreddit_dict = {"list": [], "fullnames": [], "user_subreddits": []}
    account_ages = []

    for username in username_list:
        # Iterate through the data and get the subreddit names and their
        # Reddit fullnames (prefixed with `t5_`).
        mod_target = "/user/{}/moderated_subreddits".format(username)
        for subreddit in REDDIT.get(mod_target)["data"]:
            sub_name = subreddit["sr"].lower()
            if not sub_name.startswith("u_"):
                subreddit_dict["list"].append(sub_name)
                subreddit_dict["fullnames"].append(subreddit["name"].lower())
            else:
                subreddit_dict["user_subreddits"].append(sub_name)
        # Check the age.
        account_ages.append(int(REDDIT.redditor(username).created_utc))

    # De-dupe and sort.
    subreddit_dict["list"] = list(set(subreddit_dict["list"]))
    subreddit_dict["list"].sort()
    subreddit_dict["fullnames"] = list(set(subreddit_dict["fullnames"]))
    subreddit_dict["fullnames"].sort()
    subreddit_dict["total"] = len(subreddit_dict["list"])

    # Get the age of the oldest account.
    subreddit_dict["created_utc"] = min(account_ages)

    # Get the actual PRAW objects to work with.
    if not quick_run:
        subreddit_dict["objects"] = list(REDDIT.info(fullnames=subreddit_dict["fullnames"]))

    return subreddit_dict


def get_moderator_bot_list(load_local=False):
    """This function fetches a dictionary of bots to track
    either from an online source (a Reddit wikipage)
    or a local YAML file.
    """

    if not load_local:
        source_subreddit = REDDIT.subreddit(AUTH.wiki)
        tracking_data = yaml.safe_load(source_subreddit.wiki["moderator_bots"].content_md)
    else:
        tracking_data = load_information(FILE_ADDRESS.bot_list)

    return tracking_data


def mod_list_comparator(bot_entry, new_list, original_list):
    """Function to check the differences between new and old lists.
    In the case of removals, the function also checks to see if their
    removal is due to privatization or banning.
    """
    formatted_lines = []

    changes = list(set(new_list) - set(original_list)) + list(set(original_list) - set(new_list))
    change = "* Changes for u/{}: r/{}".format(bot_entry, ", r/".join(changes))
    formatted_lines.append(change)

    # Mark down the exact changes.
    additions = [x for x in new_list if x not in original_list]
    subtractions = [x for x in original_list if x not in new_list]
    if additions:
        additions_line = "    * Additions for u/{}: r/{}".format(bot_entry, ", r/".join(additions))
        formatted_lines.append(additions_line)
    if subtractions:
        removals_line = "    * Removals for u/{}: r/{}".format(
            bot_entry, ", r/".join(subtractions)
        )
        formatted_lines.append(removals_line)

        # In the case of removals, see if something happened to the
        # subreddit. Privatized, banned?
        for entry in subtractions:
            sub_obj = REDDIT.subreddit(entry)
            try:
                subtype = sub_obj.subreddit_type
            except prawcore.exceptions.Forbidden:
                formatted_lines.append("        * Note: r/{} has gone private.".format(entry))
            except prawcore.exceptions.NotFound:
                formatted_lines.append("        * Note: r/{} has been banned.".format(entry))

    return formatted_lines


def mod_bot_comparator(quick_results=False, use_cache=True):
    """The main routine to fetch bots and their statistics
    from Reddit.
    """
    master_dictionary = {}
    comprehensive_dictionary = {}  # A dictionary to store bot statistics.
    cached_moderators = {}  # A dictionary to cache subreddit mod lists.
    quick_results_lines = []
    changes_lines = []
    line_template = "| **u/{}** | **{:,}** | {} | {} |"

    # Load cached data.
    pickled_data = load_pickled()
    if pickled_data or use_cache:
        previous_bot_data = pickled_data[0]
        previous_mod_data = pickled_data[1]
    else:
        previous_bot_data = {}
        previous_mod_data = {}

    # Initialize.
    if quick_results:
        logger.info("Getting quick results...")
    else:
        logger.info("Getting comprehensive results (will take longer)...")
    if use_cache:
        logger.info("Utilizing cached data for quicker results.")
    else:
        logger.info("Fetching fresh data for up-to-date results.")

    # Load the list of bots.
    bots_compared = get_moderator_bot_list()
    bots_list = list(bots_compared.keys())
    logger.info(f"Getting results for {len(bots_list)} bots...")

    # Get the subreddits and data associated with each bot.
    for bot_entry in bots_compared:
        user_list = bots_compared[bot_entry]
        master_dictionary[bot_entry] = get_subreddit_public_moderated(user_list, quick_results)
        total_moderated = master_dictionary[bot_entry]["total"]
        user_subs_moderated = len(master_dictionary[bot_entry]["user_subreddits"])

        # Append some quick information for the summary.
        quick_results_lines.append(
            line_template.format(bot_entry, total_moderated, len(user_list), user_subs_moderated)
        )
        logger.debug(
            "Bot u/{} moderates {:,} subreddits across its {} account(s).".format(
                bot_entry, total_moderated, len(user_list)
            )
        )

    # Display a quick summary as a Markdown table.
    quick_header = (
        "\n\n### Quick Summary\n\n"
        "| Bot | Moderated Subreddits | # Accounts | User Subreddits |\n"
        "|-----|------------|------------|----------|\n"
    )
    summary = quick_header + "\n".join(quick_results_lines)
    print(summary)

    # Permission to proceed?
    if not quick_results:
        cont_perm = input("\n> Fetch more information? y/n: ").lower()
        if cont_perm == "n" or cont_perm == "x":
            return
    else:
        return

    # Iterate per bot's subreddit objects.
    for bot_entry in bots_compared:

        logger.info(f"Now assessing u/{bot_entry}....")
        cached_bot_data = previous_bot_data.get(bot_entry, {})
        cached_count = cached_bot_data.get("total_count", 0)
        total_subscriber_count = 0
        nsfw_subs_count = 0
        quarantined_subs_count = 0
        moderator_list = []
        previously_saved_mods = {}
        modded_subs = master_dictionary[bot_entry]["objects"]
        modded_subs.sort(key=lambda x: x.display_name.lower())

        # If there's new data not the same as the cache,
        # or if a fresh run is requested.
        if not use_cache or len(modded_subs) != cached_count:

            # Calculate the differences.
            differences = mod_list_comparator(
                bot_entry,
                master_dictionary[bot_entry]["list"],
                previous_bot_data[bot_entry]["subreddits"],
            )
            if differences:
                changes_lines += differences

            # Save variables for each subreddit.
            for sub_object in master_dictionary[bot_entry]["objects"]:
                sub_name = sub_object.display_name.lower()

                place = f"{modded_subs.index(sub_object) + 1}/{len(modded_subs)}"
                logger.info(f"> (#{place}) Now checking r/{sub_name} modded by u/{bot_entry}...")

                # Get subscriber count.
                if sub_object.subscribers is None:
                    subreddit_subscribers = 0
                else:
                    subreddit_subscribers = sub_object.subscribers
                total_subscriber_count += subreddit_subscribers

                # Load moderators from cache.
                if sub_name in previous_mod_data and use_cache:
                    logger.info(
                        f">> r/{sub_name} moderator list loaded from previously saved cache."
                    )
                    previously_saved_mods = previous_mod_data[sub_name]
                elif sub_name in cached_moderators and not use_cache:
                    logger.info(
                        f">> r/{sub_name} moderator list loaded from previously accessed cache."
                    )
                    previously_saved_mods = cached_moderators[sub_name]

                # Get the relationship of moderators to the subreddit.
                if previously_saved_mods and use_cache:  # cached_moderators
                    moderator_list += previously_saved_mods  # cached_moderators
                    cached_moderators[sub_name] = previously_saved_mods
                    print(f"    > Loaded r/{sub_object} mod list from cache.")
                else:
                    try:
                        sub_mod_list = sub_object.moderator()
                        moderator_list += sub_mod_list
                    except prawcore.exceptions.Forbidden:
                        # Mod list not available.
                        print(f"    > Unable to fetch r/{sub_object} mod list.")
                        continue
                    else:
                        cached_moderators[sub_name] = sub_mod_list

                # Check if the subreddit is NSFW.
                if sub_object.over18:
                    nsfw_subs_count += 1

                # Check if the subreddit is NSFW.
                if sub_object.quarantine:
                    quarantined_subs_count += 1

            moderator_count = len(list(set(moderator_list)))
            logger.info(
                ">> Finished assessing u/{}. Total: {:,} subscribers "
                "and {:,} moderators.".format(bot_entry, total_subscriber_count, moderator_count)
            )

            comprehensive_dictionary[bot_entry] = {
                "subscribers": total_subscriber_count,
                "moderators": moderator_count,
                "subreddits": master_dictionary[bot_entry]["list"],
                "user_subreddits": master_dictionary[bot_entry]["user_subreddits"],
                "total_count": master_dictionary[bot_entry]["total"] - quarantined_subs_count,
                "quarantined_count": quarantined_subs_count,
                "nsfw_count": nsfw_subs_count,
                "created_utc": master_dictionary[bot_entry]["created_utc"],
            }
        else:
            comprehensive_dictionary[bot_entry] = previous_bot_data[bot_entry]
            logger.info(f">> Loaded u/{bot_entry} data from cache.")
            logger.info(
                ">> Finished loading u/{} data from cache. Total: {:,} subscribers "
                "and {:,} moderators.".format(
                    bot_entry,
                    comprehensive_dictionary[bot_entry]["subscribers"],
                    comprehensive_dictionary[bot_entry]["moderators"],
                )
            )

    # Save the data.
    cached_moderators.update(previous_mod_data)
    pickle_file = open(FILE_ADDRESS.pickled, "wb")
    pickle_package = (comprehensive_dictionary, cached_moderators)
    pickle.dump(pickle_package, pickle_file)
    pickle_file.close()
    with open(os.path.join(FILE_ADDRESS.output), "w", encoding="utf-8") as fp:
        json.dump(comprehensive_dictionary, fp, sort_keys=True, indent=4)

    # Display the specific changes.
    if changes_lines:
        specific_changes = "\n\n### Specific Changes\n\n" + "\n".join(changes_lines)
        print(specific_changes)

    return comprehensive_dictionary


def mod_bot_display(bot_dictionary):
    """Function that takes data from mod_bot_comparator and
    displays it in a comprehensive Markdown table.
    """
    formatted_lines = []
    line_format = "| u/{} | {:.2f} | {:,} | {} | {:.2%} | {:,} | {:,} | {:,} | {} |"

    # Format each line of the table.
    for bot in bot_dictionary:
        age = ((time.time() - bot_dictionary[bot]["created_utc"]) / 86400) / 365
        percent_nsfw = bot_dictionary[bot]["nsfw_count"] / bot_dictionary[bot]["total_count"]
        average_subscribers = int(
            bot_dictionary[bot]["subscribers"] / bot_dictionary[bot]["total_count"]
        )

        new_line = line_format.format(
            bot,
            age,
            bot_dictionary[bot]["total_count"],
            bot_dictionary[bot]["nsfw_count"],
            percent_nsfw,
            bot_dictionary[bot]["subscribers"],
            average_subscribers,
            bot_dictionary[bot]["moderators"],
            len(bot_dictionary[bot]["user_subreddits"]),
        )
        formatted_lines.append(new_line)

    # Format the presentation.
    header = (
        "\n\n### Final Data Table\n\n"
        "| Bot Name | Age (Years) | Total Moderated Subreddits | NSFW Subreddits "
        "| % NSFW | Total Subscribers | Average Subscribers / Subreddit |"
        " Total Moderators | User Subreddits |\n"
        "|----------|------|------|-------|------|---------|------|------|-----|\n"
    )
    body = header + "\n".join(formatted_lines)

    return body


# Main runtime.
if __name__ == "__main__":
    try:
        login()

        # Get user input about which mode to run it in.
        mode_type = input("\nWould you like to conduct a quick comparative run? (y/n) ")
        mode_type = mode_type.lower().strip()
        if mode_type.lower().strip() == "y":
            mode_type = True
        else:
            mode_type = False

        # Get permission to use cache.
        utilize_cache = input("\nWould you like to use any cached data? (y/n) ")
        if utilize_cache.lower().strip() == "y":
            utilize_cache = True
        else:
            utilize_cache = False

        retrieved_data = mod_bot_comparator(mode_type, utilize_cache)
        if retrieved_data:
            print(mod_bot_display(retrieved_data))
    except KeyboardInterrupt:
        # Manual termination of the script with Ctrl-C.
        logger.info("Manual user shutdown via keyboard.")
        sys.exit()
