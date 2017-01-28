﻿from lxml import html
from lxml.cssselect import CSSSelector
from urllib import request
import requests
import threading
import sys
import json
import ctypes
import datetime
import fbchat
import random
import re
import time
import calendar
import io
import urllib

from fbchat.models import Like
import stats
import quiz
import consts

CONFIG_FILE = "config.json"
STATS_FILE = "stats.json"


class ArnoldBot(fbchat.Client):
    def __init__(self, email, password, thread_fbid, config):
        self.stats = stats
        self.config = config
        self.commands = config[consts.COMMANDS]

        self.stats = stats.Stats(STATS_FILE)

        fbchat.Client.__init__(self, email, password, thread_fbid, True, None)
        threads = self.getThreadList(0)
        for item in threads:
            if item.thread_fbid == thread_fbid:
                thread = item

        # Extracts ids from participants and gets full data
        users = []
        for user_id in thread.participants:
            if user_id.startswith("fbid:"):
                users.append(user_id[5:])
        self.full_users = self.getUserInfo(users)
        
        self.annoy_list = self.stats.vals["annoy_list"]
        self.onseen_list = self.stats.vals["onseen_list"]

        self.mquiz = quiz.Quiz(config["quiz_file"], self.stats)
        self.__quiz_question_count = 0
        self.__quiz_timeout_set = False
        self.__quiz_timer = None
        

        
    def on_message(self, author_id, message, attachements, mid, metadata):
        self.markAsDelivered(author_id, mid)  # mark delivered
        self.markAsRead(author_id)  # mark read

        # Relays private messages from OPs
        if self.is_operator(author_id):
            self.group_send(message)

    def on_group_message(self, thread_fbid, author_id, message, attachements, mid, metadata):
        self.markAsDelivered(author_id, mid)
        self.markAsRead(author_id)

        # Message from myself, therefore I sent a message
        if author_id == self.uid: self.stats.updateMessagesSent()

        # If my group and not from myself
        if str(thread_fbid) == self.thread_fbid and author_id != self.uid:

            is_command = False

            # Commands
            if message.startswith("!"):
                self.log("%s sent message: '%s'" % (self.fbidToNameCode(author_id), message))

                parts = message.split(" ", 1)
                command_name = parts[0].lower()
                command_args = parts[1] if len(parts) == 2 else None

                command = self.getCommand(command_name)

                try:
                    if command:  # Command found
                        if command[consts.Cmd.IS_OPER]:  # Command is for operators
                            if self.is_operator(author_id):  # User is operator
                                getattr(self, command[consts.Cmd.ENTRY_METHOD])(author_id, command, command_args)
                            else:  # User is not operator
                                self.group_send(config[consts.COMMAND_ERROR_OPER])
                        else:  # Not operator command
                            getattr(self, command[consts.Cmd.ENTRY_METHOD])(author_id, command, command_args)

                        self.stats.updateCommandsExecuted(self.fbidToNameCode(author_id), command_name)
                    else:
                        self.cmd_simple(author_id, command_name, command_args)
                        
                    is_command = True

                except:  # Command not found
                    self.command_log_error()

            # Quiz in progress
            elif self.__quiz_timeout_set:
                self.quizGuess(author_id, message)

            if not is_command: self.global_responder(message.lower(), author_id)

            self.annoy(author_id)

    def on_group_seen(self, thread_fbid, author_id, time_seen, metadata):
        if str(thread_fbid) == self.thread_fbid and author_id != self.uid:
            # !onseen command
            for item in self.onseen_list:
                if author_id == item["to_id"]:
                    addressing_name = self.getAddressingName(self.fbidToNameCode(author_id))
                    from_nickname = self.getNickname(item["from"])
                    msg = self.commands["onseen"]["txt_onseen"] % (addressing_name, from_nickname, item["text"])
                    self.group_send(msg)
                    self.onseen_list.remove(item)
                    self.stats.makeDirty()
                    break

    def getCommand(self, command_name):
        """Returns a dict of given command with all related info. None if command not found"""
        try:
            for command in self.commands.items():
                if command_name == command[1][consts.Cmd.NAME] or command_name == command[1][consts.Cmd.SHORT]:
                    return self.commands[command[0]]
        except:
            return None
        
    def global_responder(self, message, author_id):
        """Responds, if there are words that match trigger words regex"""

        items = config["respond_to_words"]
        matches_lists = []
        message = message.lower()
        # If there are more than few matched lists, stores them
        for i, item in enumerate(items):
            # If user list is not empty checks if message is from given user
            # Doesn't find the author so goes to another set
            if item["for_users"] and self.fbidToNameCode(author_id) not in item["for_users"]:
                continue

            for word in item["triggers"]:
                # Check if only for specific users
                if re.search(word, message):
                    matches_lists.append(i)
                    break


        # There are triggers
        if len(matches_lists) > 0:
            rnd = random.randint(1, len(matches_lists)) - 1
            rnd_set = matches_lists[rnd]
            rnd = random.randint(1, len(items[rnd_set]["answers"])) - 1

            name_code = self.fbidToNameCode(author_id)
            address_name = self.getAddressingName(name_code)
            nickname = self.getNickname(name_code)

            response = items[rnd_set]["answers"][rnd]
            response = response.format(addr_name = address_name, nick = nickname)

            self.group_send(response)

    def annoy(self, author_id):
        """If person from annoy list writes a message - responds"""
        for item in self.annoy_list:
            if author_id == item["fbid"]:
                if item["count"] > 0:
                    self.group_send(item["text"])
                    item["count"] -= 1
                    break
                else:
                    self.group_send(config["commands"]["annoy"]["txt_annoy_done"])
                    self.annoy_list.remove(item)
                    break

    def command_log(self, command, param = None):
        ''' Formats command with parameters and sends to be logged
        Args:
            command - name of the executed command
            param (optional) - `str` or `dict` with arguments of the command
        '''

        msg = "Command '%s' executed." % command
        if param:
            msg += " Args: "
            if type(param) == dict:
                for key, value in param.items():
                    msg += "'%s:%s'; " % (key, value)
            else:
                msg += str(param)

        self.log(msg)

    def command_log_error(self, error = ""):
        ''' Logs an unrecognized command or some other error and sends info to chat '''
        self.log("Unrecognized command. " + error)
        self.group_send(config["command_error"])
        self.group_send(error)
        self.stats.updateCommandsError()

    def on_listening(self):
        self.group_send(config["on_login"])

    def quizRevealLetter(self, timer):
        """Reveals letter for quiz if answers are accepted and restarts timer"""
        if self.mquiz.acceptsAnswer():
            if self.mquiz.revealLetter():
                # Restarts timer
                timer = threading.Timer(self.commands["quiz"]["timeout"], self.quizRevealLetter)
                timer.args = (timer,)
                self.__quiz_timer = timer
                self.__quiz_timer.start()
                self.group_send(self.mquiz.getHiddenAnswer())
            else:
                timer.cancel()
                self.__quiz_timer = None
                self.__quiz_timeout_set = False
                msg = self.commands["quiz"]["timeout_text"] % self.mquiz.getAnswer()
                self.group_send(msg)

    def quizGiveQuestion(self):
        """Gives quiz question and sets a timer to reveal letters"""
        self.group_send("%s\n\n%s" % (self.mquiz.getQuestion(), self.mquiz.getHiddenAnswer()))

        if not self.__quiz_timeout_set:
            timer = threading.Timer(self.commands["quiz"]["timeout"], self.quizRevealLetter)
            timer.args = (timer,)
            self.__quiz_timer = timer
            self.__quiz_timer.start()
            self.__quiz_timeout_set = True

    def quizGuess(self, author_id, message):
        """Makes a quiz guess and shows new question if guess is correct"""
        # Might be an answer but answers are not accepted
        if self.mquiz.acceptsAnswer():
            points = self.mquiz.guessAnswer(self.fbidToNameCode(author_id), message)
            # Correct answer
            if points:
                nickname = self.getNickname(self.fbidToNameCode(author_id))
                msg = self.commands["quiz"]["guess_correct"] % (nickname, str(points))
                self.group_send(msg)
                self.__quiz_timer.cancel()
                self.__quiz_timer = None
                self.__quiz_timeout_set = False
                self.quizGiveQuestion()

    
    def cmd_simple(self, author_id, command_name, args):
        """Executes commands, which only respond with a text"""
        try:
            value = self.commands["simple_commands"]["commands"][command_name]

            if args == None: args = 0
            args = int(args)

            msg = self.commands["simple_commands"]["commands"][command_name][args]
            self.stats.updateCommandsExecuted(self.fbidToNameCode(author_id), command_name)
            self.group_send(msg)
        except:
            self.command_log_error()

    ############
    ### Commands
    ############
    
    def cmd_fullwidth(self, author_id, command, args):
        """Converts to full-width chars"""
        try:
            if not args: raise Exception

            args = quiz.unidecode(args)
            msg = ""

            for i in args:
                # Space doesn't convert well
                if i == " ": msg += u"　"
                else: msg += chr(0xFEE0 + ord(i))

            self.group_send(msg)

        except:
            self.command_log_error()

    def cmd_stats(self, author_id, command, args):
        """Shows current chat stats"""
        try:
            uptime = self.stats.vals["uptime_minutes"]
            times_launched = self.stats.vals["times_launched"]
            commands_executed = self.stats.vals["commands_executed"]
            commands_error = self.stats.vals["commands_error"]
            messages_sent = self.stats.vals["messages_sent"]

            msg = command[consts.Cmd.TXT_EXECUTED].format(uptime = uptime, times_launched = times_launched,\
                commands_executed = commands_executed, commands_error = commands_error, messages_sent = messages_sent)
            self.group_send(msg)

        except:
            self.command_log_error()

    def cmd_on(self, author_id, command, args):
        """Shows if bot is online right now"""
        try:
            self.group_send(command[consts.Cmd.TXT_EXECUTED] % self.stats.vals["current_uptime"])

        except:
            self.command_log_error()

    def cmd_add_addressing_name(self, author_id, command, args):
        """Adds addressing name to user who calls it"""
        try:
            if not args: raise Exception

            user = self.config[consts.Config.USERS][self.fbidToNameCode(author_id)]
            user[consts.User.ADDRESSING_NAMES].append(args)

            with open(CONFIG_FILE, "w", encoding = "utf-8") as outfile:
                json.dump(self.config, outfile, indent = "\t", ensure_ascii = False)

            self.group_send(command[consts.Cmd.TXT_EXECUTED] % args)

        except:
            self.command_log_error()

    def cmd_add_nickname(self, author_id, command, args):
        """Adds nickname to user who calls it"""
        try:
            if not args: raise Exception

            user = self.config[consts.Config.USERS][self.fbidToNameCode(author_id)]
            user[consts.User.NICKNAMES].append(args)

            with open(CONFIG_FILE, "w", encoding = "utf-8") as outfile:
                json.dump(self.config, outfile, indent = "\t", ensure_ascii = False)

            self.group_send(command[consts.Cmd.TXT_EXECUTED] % args)

        except:
            self.command_log_error()


    def cmd_weather(self, author_id, command, args):
        """Shows weather by scraping my local weather site"""
        try:
            date = time.strftime("%Y%m%d", time.localtime())
            # Vilnius
            page = requests.get("http://www.meteo.lt/lt_LT/miestas?placeCode=Vilnius")
            tree = html.fromstring(page.content)
            td = tree.cssselect("div.weather_info.type_1")[0]
            td_temp_vln = td.cssselect("span.temperature")[0].text
            if int(td_temp_vln) > 0: td_temp_vln = "+" + td_temp_vln
            td_type_vln = td.cssselect("span.large.condition")[0].get("title").lower()

            tmrw = tree.cssselect("div.portlet-body div.weather_block_city div.slider")[0].getchildren()[1]
            tmrw = tmrw.cssselect("a")[0].getchildren()
            tmrw_night_temp_vln = tmrw[2][2].text[:-3]
            if int(tmrw_night_temp_vln) > 0: tmrw_night_temp_vln = "+" + tmrw_night_temp_vln
            tmrw_night_type_vln = tmrw[2][1].get("title").lower()
            tmrw_day_temp_vln = tmrw[3][2].text[:-3]
            if int(tmrw_day_temp_vln) > 0: tmrw_day_temp_vln = "+" + tmrw_day_temp_vln
            tmrw_day_type_vln = tmrw[3][1].get("title").lower()

            # Kaunas
            page = requests.get("http://www.meteo.lt/lt_LT/miestas?placeCode=Kaunas")
            tree = html.fromstring(page.content)
            td = tree.cssselect("div.weather_info.type_1")[0]
            td_temp_kns = td.cssselect("span.temperature")[0].text
            if int(td_temp_kns) > 0: td_temp_kns = "+" + td_temp_kns
            td_type_kns = td.cssselect("span.large.condition")[0].get("title").lower()

            tmrw = tree.cssselect("div.portlet-body div.weather_block_city div.slider")[0].getchildren()[1]
            tmrw = tmrw.cssselect("a")[0].getchildren()
            tmrw_night_temp_kns = tmrw[2][2].text[:-3]
            if int(tmrw_night_temp_kns) > 0: tmrw_night_temp_kns = "+" + tmrw_night_temp_kns
            tmrw_night_type_kns = tmrw[2][1].get("title").lower()
            tmrw_day_temp_kns = tmrw[3][2].text[:-3]
            if int(tmrw_day_temp_kns) > 0: tmrw_day_temp_kns = "+" + tmrw_day_temp_kns
            tmrw_day_type_kns = tmrw[3][1].get("title").lower()

            # Panevėžys
            page = requests.get("http://www.meteo.lt/lt_LT/miestas?placeCode=Panevezys")
            tree = html.fromstring(page.content)
            td = tree.cssselect("div.weather_info.type_1")[0]
            td_temp_pnvz = td.cssselect("span.temperature")[0].text
            if int(td_temp_pnvz) > 0: td_temp_pnvz = "+" + td_temp_pnvz
            td_type_pnvz = td.cssselect("span.large.condition")[0].get("title").lower()

            tmrw = tree.cssselect("div.portlet-body div.weather_block_city div.slider")[0].getchildren()[1]
            tmrw = tmrw.cssselect("a")[0].getchildren()
            tmrw_night_temp_pnvz = tmrw[2][2].text[:-3]
            if int(tmrw_night_temp_pnvz) > 0: tmrw_night_temp_pnvz = "+" + tmrw_night_temp_pnvz
            tmrw_night_type_pnvz = tmrw[2][1].get("title").lower()
            tmrw_day_temp_pnvz = tmrw[3][2].text[:-3]
            if int(tmrw_day_temp_pnvz) > 0: tmrw_day_temp_pnvz = "+" + tmrw_day_temp_pnvz
            tmrw_day_type_pnvz = tmrw[3][1].get("title").lower()
            
            msg = command["txt_executed"] % ("Vilniuje", td_temp_vln, td_type_vln,\
                tmrw_day_temp_vln, tmrw_day_type_vln, tmrw_night_temp_vln, tmrw_night_type_vln) + "\n\n"
            msg += command["txt_executed"] % ("Kaune", td_temp_kns, td_type_kns,\
                tmrw_day_temp_kns, tmrw_day_type_kns, tmrw_night_temp_kns, tmrw_night_type_kns) + "\n\n"
            msg += command["txt_executed"] % ("Panevėžyje", td_temp_pnvz, td_type_pnvz,\
                tmrw_day_temp_pnvz, tmrw_day_type_pnvz, tmrw_night_temp_pnvz, tmrw_night_type_pnvz)

            self.group_send(msg)
            pass
        except:
            self.command_log_error()


    def cmd_urban_dict(self, author_id, command, args):
        """Shows urban dictionary entry"""
        try:
            if args == None: raise Exception
            
            # Number of defitinios
            count = 1
            if re.match(r"\s\d", args[-2:]):
                count = int(args[-1])

            args = args[:-2].replace(" ", "+")
            url = "http://api.urbandictionary.com/v0/define?term="
            url += args

            response = json.load(urllib.request.urlopen(url))


            # Page not found
            if response["result_type"] == "no_results":
                self.group_send(command["txt_error"])
                return

            if count < 1: count = 1
            if count > len(response["list"]): count = len(response["list"])

            msg = ""
            for i in range(count):
                result = response["list"][i]
                word = result["word"]
                definition = result["definition"]
                example = result["example"]

                msg += command[consts.Cmd.TXT_EXECUTED] % ((i + 1), word, definition, example)
            
            msg += response["list"][0]["permalink"]
            self.group_send(msg)
        except:
            self.command_log_error()


    def cmd_wikipedia(self, author_id, command, args):
        """Shows extract from wikipedia"""
        try:
            if args == None: raise Exception
            
            args = args.replace(" ", "_")
            url = "https://en.wikipedia.org/w/api.php?format=json&action=query&prop=extracts&exintro=&explaintext=&titles="
            url += args

            response = json.load(urllib.request.urlopen(url))

            extract = response["query"]["pages"]

            # Page not found
            if "-1" in extract:
                self.group_send(command["txt_error"])
                return
            extract = next(iter(extract.values()))

            url_to_add = "https://en.wikipedia.org/wiki/" + args
            msg = extract["extract"] + "\n" + url_to_add
            
            self.group_send(msg)
        except:
            self.command_log_error()

            
    def cmd_unfair_roll(self, author_id, command, args):
        """The person being played doesn't know lol"""
        try:
            unfair_list = []
            for key, value in command["rolls"].items():
                unfair_list += [key] * int(value)

            msg = random.choice(unfair_list)
            self.group_send(msg, like = Like.small)


        except:
            self.command_log_error()

    def cmd_roll(self, author_id, command, args):
        """The person being played doesn't know lol"""
        try:
            args = int(args)
            roll = random.randint(0, args)
            msg = command[consts.Cmd.TXT_EXECUTED] % (self.getNickname(self.fbidToNameCode(author_id)), roll)
            self.group_send(msg)


        except:
            self.command_log_error()


    def cmd_onseen(self, author_id, command, args):
        """Writes a message when person opens up chat"""
        try:
            args = args.split(" ", 1)
            if len(args) != 2: raise Exception
            
            name = args[0]
            text = args[1]
            to_id = self.nameToFbid(name)
            to_name_code = self.fbidToNameCode(to_id)
            from_name_code = self.fbidToNameCode(author_id)

            list_item = {"from": from_name_code, "to": to_name_code, "to_id": to_id, "text": text}
            self.onseen_list.append(list_item)
            self.stats.makeDirty()

            self.group_send(command["txt_executed"])

        except:
            self.command_log_error()

    def cmd_simpleCommands(self, author_id, command, args):
        """Adds a command to simple_commands list."""
        try:
            args = args.split(" ", 1)
            new_command_name = "!" + args[0]

            # Appending to list
            if new_command_name in command["commands"].keys():
                command["commands"][new_command_name].append(args[1])
            else:
                command["commands"][new_command_name] = [args[1]]

            with open(CONFIG_FILE, "w", encoding = "utf-8") as outfile:
                json.dump(config, outfile, indent = "\t", ensure_ascii = False)

            self.group_send(command[consts.Cmd.TXT_EXECUTED] % new_command_name)

        except:
            self.command_log_error()

    def cmd_quiz(self, author_id, command, args):
        """Trivia game"""
        # Quiz config entry
        name_code = self.fbidToNameCode(author_id)

        if args == None:
            args = command[consts.Cmd.ARGS]["help"]

        try:
            # Checks if it is a command
            for key, val in command[consts.Cmd.ARGS].items():
                if val == args:
                    if key == "help":
                        self.group_send(command["help"])
                        self.command_log(command[consts.Cmd.NAME], args)

                    elif key == "repeat_question":
                        self.command_log(command[consts.Cmd.NAME], args)
                        if self.__quiz_timeout_set:
                            self.group_send("%s\n\n%s" % (self.mquiz.getQuestion(), self.mquiz.getHiddenAnswer()))
                        else:
                            self.quizGiveQuestion()

                    elif key == "get_question":
                        self.command_log(command[consts.Cmd.NAME], args)
                        self.quizGiveQuestion()

                    elif key == "user_stats":
                        self.command_log(command[consts.Cmd.NAME], args)
                        stats = self.mquiz.getUserStats(self.fbidToNameCode(author_id))
                        if stats:
                            msg = command["user_stats_text"].format(name = self.fbidToName(author_id), **stats)
                        else:
                            msg = command["user_not_played"]
                        self.group_send(msg)

                    elif key == "global_stats":
                        self.command_log(command[consts.Cmd.NAME], args)
                        stats = self.mquiz.getGlobalStats()
                        msg = command["global_stats_text"].format(**stats)
                        self.group_send(msg)

                    elif key == "top_3":
                        self.command_log(command[consts.Cmd.NAME], args)
                        users = self.mquiz.getTop(3)

                        msg = command["top_text"]
                        for i, user in enumerate(users):
                            msg += command["top_position_text"] % (str(i + 1), user[0], user[1][quiz.POINTS])
                        self.group_send(msg)

                    break
            # Command not found, it is a guess
            else:
                self.quizGuess(author_id, " ".join(args))
        except Exception as e:
            self.command_log_error(str(e))

    def cmd_annoy(self, author_id, command, args):
        """Writes a message after the person to be annoyed writes"""
        try:
            args = args.split(" ", 2)
            if len(args) != 3: raise Exception
            name = args[0]
            text = args[2]
            count = int(args[1])
            if count > command["annoy_limit"]: raise Exception
                        
            fbid = self.nameToFbid(name)
            if fbid == self.uid: raise Exception

            name_code = self.fbidToNameCode(fbid)
            args = {"name_code": name_code, "fbid": fbid, "count": count, "text": text}
            self.annoy_list.append(args)

            self.command_log(command[consts.Cmd.NAME], args)

            self.group_send(command[consts.Cmd.TXT_EXECUTED])

        except:
            self.command_log_error()

    def cmd_unannoy(self, author_id, command, args):
        """Removes author from annoy list"""
        for item in self.annoy_list:
            if item["fbid"] == author_id:
                self.command_log(command[consts.Cmd.NAME])
                self.group_send(command[consts.Cmd.TXT_EXECUTED])
                self.annoy_list.remove(item)
                break;

    def cmd_say(self, author_id, command, args):
        """Repeats what user said"""
        if args:
            self.command_log(command[consts.Cmd.NAME], args)
            self.group_send(args)
        else:
            self.command_log(command[consts.Cmd.NAME])
            self.group_send(command[consts.Cmd.TXT_ARGS_ERROR])

    def cmd_updateconfig(self, author_id, command, args):
        """Updates config with new values"""
        try:
            with open(CONFIG_FILE, encoding = "utf-8") as infile:
                new_config = json.load(infile)
            config.update(new_config)
            self.command_log(command[consts.Cmd.NAME])
            self.group_send(command[consts.Cmd.TXT_EXECUTED])
        except Exception as e:
            self.group_send(e)
            self.command_log_error()

    def cmd_savestats(self, author_id, command, args):
        """Force saves all stats"""
        try:
            self.stats.updateCommandsExecuted(self.fbidToNameCode(author_id), command[consts.Cmd.NAME])
            self.stats.updateStats()
            self.command_log(command[consts.Cmd.NAME])
            self.group_send(command[consts.Cmd.TXT_EXECUTED])
        except Exception as e:
            self.group_send(e)
            self.command_log_error()


    def cmd_time(self, author_id, command, args):
        """Displays current time"""
        self.command_log(command[consts.Cmd.NAME])

        now = time.time()
        dtnow = datetime.datetime.now()
        local_time = time.localtime()

        msg = time.strftime(command["txt_format"].decode("utf-8"), local_time)
        # msg += "Unix time: %s\n\n" % str(int(now))
        cal = calendar.TextCalendar(calendar.MONDAY).formatmonth(dtnow.year, dtnow.month)
        cal = cal.replace("  ", "   ")
        cal = cal.replace("\n ", "\n  ")
        msg += cal

        self.group_send(msg)
        
        
    def cmd_save_user_list(self, author_id, command, args):
        """Saves current users to config"""
        try:
            threads = self.getThreadList(0)
            for item in threads:
                if item.thread_fbid == self.thread_fbid:
                    thread = item

            # Extracts ids from participants and gets full data
            users = []
            for user_id in thread.participants:
                if user_id.startswith("fbid:"):
                    users.append(user_id[5:])
            self.full_users = self.getUserInfo(users)

            added = 0
            marked_in_chat = 0
            for user in self.full_users:
                name_code = self.fbidToNameCode(user["id"])
                # User is not in config
                if name_code not in self.config[consts.Config.USERS]:
                    new_user = {
                        consts.User.ID: user["id"],
                        consts.User.NAME: user["firstName"],
                        consts.User.FULL_NAME: user["name"],
                        consts.User.GENDER: user["gender"],
                        consts.User.URL: user["uri"],
                        consts.User.IN_CHAT: True,
                        consts.User.IS_FRIEND: user["is_friend"],
                        consts.User.NICKNAMES: [],
                        consts.User.ADDRESSING_NAMES: []}
                    self.config[consts.Config.USERS][name_code] = new_user
                    added += 1
                # User is in config but was marked as not in chat
                elif not self.config[consts.Config.USERS][name_code][consts.User.IN_CHAT]:
                    self.config[consts.Config.USERS][name_code][consts.User.IN_CHAT] = True
                    marked_in_chat += 1

            # User is in config, but not in chat
            marked_removed = 0
            for key, val in self.config[consts.Config.USERS].items():
                for user_in_chat in self.full_users:
                    if val[consts.User.ID] == user_in_chat["id"]:
                        break
                else:
                    user_in_config[consts.User.IN_CHAT] = False
                    marked_removed += 1
                    
            with open(CONFIG_FILE, "w", encoding = "utf-8") as outfile:
                json.dump(self.config, outfile, indent = "\t", ensure_ascii = False)
            

            self.group_send(command[consts.Cmd.TXT_EXECUTED] % (added, marked_in_chat, marked_removed))

        except Exception as e:
            self.group_send(e)
            self.command_log_error()
            

    def cmd_help(self, author_id, command, args):
        """Shows all available commands"""
        try:
            msg = command[consts.Cmd.TXT_EXECUTED]
            sorted_list = sorted(self.commands.items(), key = lambda tup: tup[1][consts.Cmd.NAME])
            for cmd in sorted_list:
                if not cmd[1][consts.Cmd.IS_OPER]:
                    msg += cmd[1][consts.Cmd.NAME]
                    if cmd[1][consts.Cmd.SHORT] != "": msg += " (%s)" % cmd[1][consts.Cmd.SHORT]
                    msg += " - " + cmd[1][consts.Cmd.INFO] + "\n"

            self.group_send(msg)

        except Exception as e:
            self.group_send(e)
            self.command_log_error()


    def fbidToName(self, fbid):
        for user in self.full_users:
            if user["id"] == fbid:
                return user["firstName"]

    def nameToFbid(self, name):
        for user in self.full_users:
            if user["firstName"].lower() == name:
                return user["id"]

    def fbidToNameCode(self, fbid):
        for user in self.full_users:
            if user["id"] == fbid:
                return user["firstName"] + fbid[-3:]

    def nameCodeToFbid(self, name_code):
        for user in self.full_users:
            if user["id"][-3:] == name_code[-3:] and user["firstName"] == user["id"][:-3]:
                return user["id"]
    
    def getAddressingName(self, name_code):
        user = self.config[consts.Config.USERS].get(name_code, None)
        if user:
            addr_names = user[consts.User.ADDRESSING_NAMES]
            if addr_names:
                rnd = random.randint(0, len(addr_names) - 1)
                return addr_names[rnd]
            return user[consts.User.NAME]
        return name_code

    def getNickname(self, name_code):
        user = self.config[consts.Config.USERS].get(name_code, None)
        if user:
            nicknames = user[consts.User.NICKNAMES]
            if nicknames:
                rnd = random.randint(0, len(nicknames) - 1)
                return nicknames[rnd]
            return user[consts.User.NAME]
        return name_code


    def nameToNameCode(self, name):
        return self.fbidToNameCode(self.nameToFbid(name))

    def is_operator(self, fbid):
        """Returns if given FBID is chatroom operator"""
        return fbid in config["oper_fbid_list"]


# Config and stats files provided via argument
if len(sys.argv) >= 3:
    CONFIG_FILE = sys.argv[1]
    STATS_FILE = sys.argv[2]

with open(CONFIG_FILE, encoding = "utf-8") as infile:
    config = json.load(infile)

ctypes.windll.kernel32.SetConsoleTitleW(config["bot_name"])

bot = ArnoldBot(config[consts.Config.EMAIL], config[consts.Config.PASSWORD], config[consts.Config.THREAD_FBID], config)
bot.listen()
