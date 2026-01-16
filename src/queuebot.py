"""
Author: Benjamin Perumala

See README.md for details on how to use QueueBot

NOTICES:
    - This is the first time I've used asyncio so best practices, etc.
    may not have been entirely followed.

    - This bot has a "global" queue and, as a result, expects to run on a single
    server (using a single bot in multiple servers will not work). This can easily
    be solved by having the queue be a dictionary that maps a server to a queue
    but is out of scope for the project. (QueueBot.update_presence() would likely
    need to be removed as well since each server would have a different queue size)

    - The bot does not use discord.py's commands.Cogs or commands.Bot features.
    This was intentionally done to try and make it easier for beginner programmers
    to understand how different portions of the code interact and work together
"""

import os
import sys
import logging
import logging.handlers
import asyncio
import discord  
from datetime import datetime
import constants

from collections import deque

from config import QueueConfig, get_config_json
from utils import CmdPrefix, DiscordUser, log_session


# TODO Notify user if they're in voice channel and not in queue? https://discordpy.readthedocs.io/en/latest/ext/tasks/index.html
# TODO Save Queue State in the case of a restart?
# TODO Make all commands private
# QueueBot extends the discord.Client class

class QueueBot(discord.Client):
    """
    Instantiate the QueueBot that connects to a Discord server to manage a single queue

    Parameters:
        config: A QueueConfig object specifying config options
        logger: A logger object created from Python's logging module
    """

    def __init__(self, config, logger, testing=False):
        assert isinstance(config, QueueConfig)

        # Tell Discord library what events we want and don't want
        # We don't want events for people typing, going online,
        # DMing the bot, etc. All we really want is events for messages
        intents = discord.Intents.default()
        intents.typing = False
        intents.presences = False
        intents.dm_messages = False
        intents.invites = False
        intents.messages = True
        intents.message_content = True

        # Cache voice channels only if queuebot checks voice channel state
        intents.members = True if config.CHECK_VOICE_WAITING or config.ALERT_ON_FIRST_JOIN else False
        super().__init__(intents=intents)  # Calls __init__() on super class (discord.Client)

        self._testing = testing
        self._is_initialized = False
        self._config = config
        self._logger = logger
        self._join_times = {}
        self._queues = {}  # guild -> doubly linked list

    async def on_ready(self):
        """
        Discord.py calls this on initialization (does not run in testing mode)
        It does some setup and saves the waiting room voice channel to self.waiting_room

        Returns: None
        """
        self._is_initialized = False  # set back to initializing state (in case bot reconnects)
        self._logger.info('Logged in as {0}!'.format(self.user))

        if len(self.guilds) == 0:
            self._logger.error("The bot is not connected to any servers! " +
                              "Please add the bot to a server as shown in the README")
            exit(0)

        guild = self.guilds[0]
        self._logger.info(f"Found server '{guild.name}'")

        if self._testing:
            self._is_initialized = True
            return

        if self._config.CHECK_VOICE_WAITING:
            self._waiting_room = self._get_channel_from_name(self._config.VOICE_WAITING, guild.voice_channels).pop()

        await self.change_presence(activity=discord.Game(name="Type '!q help' for all commands"))
        self._is_initialized = True
        self._logger.info(f"Found all voice and text channels. Ready to process requests.")

    # TODO Documentation
    # Names either list/tuple of strings or a string
    def _get_channel_from_name(self, names, all_channels):
        # TODO Assert all voice channels names are unique
        names = set([names]) if isinstance(names, str) else set(names)
        channel_objects = set(filter(lambda c: c.name in names, all_channels))

        if len(names) == len(channel_objects):
            return channel_objects

        # Could not find all channels
        # TODO Make debug output more helpful (is it looking for office hours? Waiting room?)
        missing = names - set([v.name for v in channel_objects])
        self._logger.error("Unable to find the following channels: " +
                            ", ".join([f"'{v}'" for v in missing]))
        self._logger.error("Available channels: " +
                            ", ".join([f"'{c.name}'" for c in channel_objects]))
        sys.exit(1)  # FIXME Exit traceback is very messy

    # TODO Documentation
    async def on_message(self, message):
        # Bot still initializing; not ready to receieve messages
        if not self._is_initialized:
            return

        # Ignore own messages
        if message.author == self.user:
            return

        # Ignore DMs and other message events
        if not isinstance(message.channel, discord.channel.TextChannel):
            return

        # Ignore channels that are not part of TEXT_LISTENS config item
        # TODO Update to check config
        if message.channel.name not in self._config.TEXT_LISTENS:
            return

        self._logger.info('[#{0.channel}] {0.author} ({0.author.id}): {0.content}'.format(message))

        # All QueueBot commands start with !q
        if message.content[:2].lower().startswith("!q"):
            try:
                update = await self._queue_command(message)

                if update:
                    await self._log_queue_state(message.channel)
            except discord.errors.Forbidden:
                    await self._send(message.channel, "Unable to send message! User and/or channel privacy settings likely preventing the message from being received", message_type=CmdPrefix.ERROR)
            except Exception as e:
                self._logger.error(e)
                await self._send(message.channel, "An error has occurred.", CmdPrefix.ERROR)
                raise e

    async def _log_queue_state(self, channel):
        """
        Update the bot's profile activity to show how many people
        are in the queue

        Returns: None
        """
        retval = []
        queue = self.get_queue(channel)
        for user in queue:
            state = "in-person" if user.is_inperson() else "online"
            retval.append(f"{user} (state='{state}' join={user.get_join_time()})")

        self._logger.info("\tQueue state: " + ", ".join(retval))

    def get_queue(self, channel):
        if channel.guild not in self._queues:
            self._queues[channel.guild] = deque()
        return self._queues[channel.guild]

    # TODO Use message.reply instead of message.send()? Double check parameters
    async def _send(self, channel, content=None, message_type=None, *, embed=None, allowed_mentions=None):
        """
        Simple wrapper of discord.py's send method.
        This is used to add emote prefixes to messages as well as
        facilitate unit testing by printing out messages to stdout
        """
        if not self._is_initialized:
            pass  # TODO Do something (eat messages...? Could cause confusion)

        prefix_emote = ""
        if message_type is CmdPrefix.WARNING:
            prefix_emote = "⚠️"
        elif message_type is CmdPrefix.SUCCESS:
            prefix_emote = "✅"
        elif message_type is CmdPrefix.ERROR:
            prefix_emote = "‼️"

        if prefix_emote:
            content = prefix_emote + " " + content

        if not self._testing:
            self._logger.info(f"[#{channel.name}] {self.user} [embed? {embed is not None}] {content.rstrip() if content else ''}")
            return await channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)  # TODO pass in kwargs/args?
        else:
            print("SEND:", content, end="")
            if embed:
                print(f" embed.title='{embed.title}', embed.description={embed.description}, fields={embed.fields}")
            else:
                print()  # End current line

    # TODO Combine with send() as code is identical aside from send/send logging
    async def _send_dm(self, user, content=None, message_type=None, *, embed=None, log_message=True, file=None):
        if not self._is_initialized:
            pass  # TODO Do something (eat messages...? Could cause confusion if connection randomly drops)

        prefix_emote = ""
        if message_type is CmdPrefix.WARNING:
            prefix_emote = "⚠️"
        elif message_type is CmdPrefix.SUCCESS:
            prefix_emote = "✅"
        elif message_type is CmdPrefix.ERROR:
            prefix_emote = "‼️"

        if prefix_emote:
            content = prefix_emote + " " + content

        if self._testing:
            print("SEND DM:", content, end="")
            if embed:
                print(f" embed.title='{embed.title}', embed.description={embed.description}, fields={embed.fields}", end="")
            print()  # End current line
            return None

        if log_message:
            self._logger.info(f"[Direct Message] {self.user} --> {user} ({user.id}): [embed? {embed is not None}] {content.rstrip() if content else ''}")

        if file is None:
            return await user.send(content=content, embed=embed)  # TODO pass in kwargs/args?
        return await user.send(file=discord.File(file))

    def _is_ta(self, user_roles, ta_roles):
        """
        Checks to see if a given user's role list is a TA
        from config.TA_ROLES

        Parameters:
            user_roles: A discord.py user's role list to check
            ta_roles: List of strings that is considered to be TA roles

        Returns: True if the user is a TA (False otherwise)
        """
        for r in user_roles:
            if r.name in ta_roles:
                return True
        return False

    async def _queue_command(self, message):
        """
        Takes a !q ______ command and attempts to parse it
        discord.py likely has a better way to do this but I
        did this option for the sake of simplicity

        Parameters:
            message: A discord.py message object where the message starts with '!q'

        Returns: True if queue updated (False otherwise)
        """
        full_command = message.content.split()
        full_command = list(map(str.lower, full_command))  # lower case entire message's content
        channel = message.channel
        author = message.author

        # TODO Account for MockAuthor
        # if isinstance(author, MockAuthor):
        #     user = DiscordUser(author.id, author.name, author.nick, author.roles)
        if isinstance(author, discord.member.Member):
            user = DiscordUser(author.id, author.name, author.discriminator, author.nick)
        elif isinstance(author, discord.user.User):
            # Users don't have nicknames
            user = DiscordUser(author.id, author.name, author.discriminator, None)
        else:
            # TODO Don't put author in error message (bad practice? Double check)
            raise ValueError(f"{type(author)} is an unknown author type")

        if len(full_command) < 2 or len(full_command) > 3:
            # TODO Combine this and other invalid format/syntax commands into single constant
            await self._send(channel, f"{user.get_mention()} invalid syntax. " +
                "Type `!q join` to join the queue or `!q leave` to leave.\n" +
                "(see `!q help` for all commands)", CmdPrefix.WARNING)
            # await self.send(channel, f"{user.get_mention()} invalid syntax. " +
            #     "Type `!q join-inperson` if you are in person (`!q join` for online) to join the queue or `!q leave` to leave.\n" +
            #     "(see `!q help` for all commands)", CmdPrefix.WARNING)
            return False

        command = full_command[1]

        """ STUDENT COMMANDS """

        # TODO If a student joined online and is inperson, !q join-inperson whould update their state to in-person (and vise-versa)
        # TODO Temporarily hide in-person
        if command == "ping":
            return await self._q_ping(channel)
        elif command == "help":
            return await self._q_help(user, channel, message.author)
        # TODO !q join-online and !q join (in-person)
        elif command == "join" and \
                len(full_command) > 2 and full_command[2] in {"in-person", "inperson", "in"}:
            return await self.q_join_inperson(user, channel)
        elif command == "join":
            return await self._q_join(user, channel)
        elif command == "join-inperson":
            return await self._q_join_inperson(user, channel)
        elif command == "leave":
            return await self._q_leave(user, channel)
        elif command == "position" or command == "pos":
            return await self._q_position(user, channel)
        elif command == "list":
            return await self._q_list(user, channel)

        """ TA COMMANDS """

        # Make sure user is a TA for rest of commands
        ta_roles = self._config.TA_ROLES
        if not self._is_ta(author.roles, ta_roles):
            await self._send(channel, f"{user.get_mention()} invalid format. " +
                "Type `!q join-inperson` if you are in person (`!q join` for online) to join the queue or `!q leave` to leave.\n" +
                "(see `!q help` for all commands)" , CmdPrefix.WARNING)
            return False

        if len(full_command) == 2:
            # TODO Option to skip over students in the queue who are in an office hour room
            if command == "next" or command == "remove" or command == "pop":
                return await self._q_next(user, channel)
            elif command == "clear" or command == "empty":
                return await self._q_clear(user, channel)
            elif command == "logs":
                return await self._q_logs(user, channel)

        # Don't check for length (user could accidentally write out name - including spaces - instead of mentioning)
        # As a result, the command will account for it and print out the necessary warning message
        if command == "add":
            return await self._q_add_other(user, message.mentions, channel)
        if command == "add-inperson":
            return await self.q_add_other(user, message.mentions, channel, in_person=True)
        elif command == "remove":
            return await self._q_remove_other(user, message.mentions, channel)
        elif command == "front":
            return await self._q_move_front_other(user, message.mentions, channel)

        # Didn't find matching command
        # await self.send(channel, f"{user.get_mention()} invalid format. Type `!q join-inperson` if you are in person (`!q join` for online) to join the queue or `!q leave` to leave.", CmdPrefix.WARNING)
        await self._send(channel, f"{user.get_mention()} invalid format. Type `!q join` (after joining the waiting room) to join the queue or `!q leave` to leave.", CmdPrefix.WARNING)
        return False

        # TODO Before removing someone from the queue, log duration that user was in the queue

    async def _q_ping(self, channel):
        """
        If a user sends !q ping, reply with "Pong!"
        *Can be run by anyone*

        Parameters:
            channel: discord.py channel object to send message to

        Returns: False (doesn't update queue)
        """
        await self._send(channel, "Pong!")
        return False

    async def _q_help(self, user, channel, author):
        """
        If a user sends !q help, send a Direct Message to a given user with a
        list of available commands If they have a TA role, it will list student
        commands as well as TA commands
        *Can be run by anyone*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            channel: discord.py channel to send the message to
            author: discord.py user associated with user parameter (used to check roles)

        Returns: False (doesn't update queue)
        """
        discord_user = self.get_user(user.get_uuid())
        commands = f"{constants.MSG_HELP['STUDENT']}"

        if self._is_ta(author.roles, self._config.TA_ROLES):
            commands += "\n\n" + constants.MSG_HELP["TA"]
            self._logger.info("\t> Sent TA help command")
        else:
            self._logger.info("\t> Sent Student help command")

        await self._send_dm(discord_user, commands, log_message=False)
        await self._send(channel, f"{user.get_mention()} a list of the commands has been sent to your Direct Messages", CmdPrefix.SUCCESS)
        return False

    async def _alert_avail_tas(self, channel):
        """
        Notify available TAs when someone joins the queue
        (where an available TA is a TA who is in an office hours
        room without a student in it)

        Returns: Number of TAs mentioned
        """
        if not self._config.ALERT_ON_FIRST_JOIN:
            return

        self._logger.debug("\t> Getting active TAs for ALERT_ON_FIRST_JOIN")

        actives = []

        voice_offices = self._get_channel_from_name(self._config.VOICE_OFFICES, channel.guild.voice_channels)
        for room in voice_offices:
            # All members in the channel are TAs
            if all(self._is_ta(user.roles, self._config.TA_ROLES) for user in room.members):
                actives.extend(room.members)

        if len(actives) == 0:
            self._logger.debug("\t> No active TAs to alert about nonempty queue")
            return 0

        self._logger.debug(f"\t> Active TAs: {actives}")
        message = " ".join([ta.mention for ta in actives]) + " The queue is no longer empty"
        await self._send(channel, message)
        return len(actives)

    async def _q_join(self, user, channel):
        """
        If a user sends "!q join", attempt to add them to the queue
        The user must be within the config.WAITING_ROOM voice channel before joining
        *Can be run by anyone*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            channel: discord.py channel object to send message to

        Returns: True if the user is added to the queue
        """
        # TODO Use function for checking if user in waiting room
        if self._config.CHECK_VOICE_WAITING and not in_voice_channel(user, channel, self._waiting_room.name):
            # await self.send(channel, f"{user.get_mention()} Please join the __{self._waiting_room.name}__ voice channel then __run `!q join` again__\n(if you are in Gould-Simpson waiting for office hours use `!q join-inperson` instead)", CmdPrefix.WARNING)
            await self._send(channel, f"{user.get_mention()} Please join the __{self._waiting_room.name}__ voice channel then __run `!q join` again__", CmdPrefix.WARNING)
            return False

        queue = self.get_queue(channel)

        if user in queue:
            index = queue.index(user)
            q_user = queue[index]
            if not q_user.is_inperson():
                await self._send(channel, f"{user.get_mention()} you are already in the queue at position #{index+1}", CmdPrefix.WARNING)
            else:
                q_user.set_inperson(False)
                await self._send(channel, f"{user.get_mention()} status changed to *online* (position in queue: {index+1})", CmdPrefix.SUCCESS)
            return False

        queue.append(user)
        self._join_times[user.get_uuid()] = datetime.now()

        if len(queue) == 1:
            await self._alert_avail_tas(channel)
        await self._send(channel, f"""{user.get_mention()} you have been added at position #{len(queue)} *(online)*\n*Please stay in the voice channel while you wait*""", CmdPrefix.SUCCESS)
        return True

    async def _q_join_inperson(self, user, channel):
        """
        If a user sends "!q join-inperson", attempt to add them to the queue
        The user must be within the config.WAITING_ROOM voice channel before joining
        *Can be run by anyone*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            channel: discord.py channel object to send message to

        Returns: True if the user is added to the queue
        """

        queue = self.get_queue(channel)

        if user in queue:
            index = queue.index(user)
            q_user = queue[index]
            if q_user.is_inperson():
                await self._send(channel, f"{user.get_mention()} you are already in the queue at position #{index+1}", CmdPrefix.WARNING)
            else:
                q_user.set_inperson(True)
                await self._send(channel, f"{user.get_mention()} status changed to __*in-person*__ (position in queue: {index+1})", CmdPrefix.SUCCESS)
            return False

        user.set_inperson(True)
        queue.append(user)
        self._join_times[user.get_uuid()] = datetime.now()

        self._logger.debug("Queue length after adding user = " + str(len(queue)))
        if len(queue) == 1:
            await self._alert_avail_tas(channel)
        await self._send(channel, f"""{user.get_mention()} you have been added at position #{len(queue)} *(in-person)*""", CmdPrefix.SUCCESS)
        return True

    async def _q_leave(self, user, channel):
        """
        If a user sends "!q leave", attempt to remove them to the queue
        *Can be run by anyone*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            channel: discord.py channel object to send message to

        Returns: True if the user is removed from the queue
        """

        queue = self.get_queue(channel)

        if user in queue:
            queue.remove(user)
            await self._send(channel, f"{user.get_mention()} you have been removed from the queue", CmdPrefix.SUCCESS)
            await log_session(user.get_name(), self._join_times.get(user.get_uuid(), None), None, "leave", channel.guild.name)
            return True
        else:
            await self._send(channel, f"{user.get_mention()} you can not be removed from the queue because you never joined it", CmdPrefix.WARNING)
            return False

    async def _q_position(self, user, channel):
        """
        If a user sends "!q position", tell them their position within the queue
        *Can be run by anyone*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            channel: discord.py channel object to send message to

        Returns: False (doesn't update queue)
        """

        queue = self.get_queue(channel)

        if user in queue:
            index = queue.index(user) + 1
            await self._send(channel, f"{user.get_mention()} you are at position #{index}")
        else:
            await self._send(channel, f"{user.get_mention()} you are not in the queue")

        return False

    async def _q_next(self, user, channel):
        """
        If a user sends "!q pop" or "!q next", removes the next person from the queue
        *Must be run by a TA*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            channel: discord.py channel object to send message to

        Returns: True if a user is removed
        """
        queue = self.get_queue(channel)

        if len(queue) == 0:
            await self._send(channel, "Queue is empty")
            return False

        q_next = queue.popleft()
        await log_session(q_next.get_name(), self._join_times.get(q_next.get_uuid(), None), user.get_name(), "next", channel.guild.name)

        # TODO Verify debug message is useful and easy to parse
        self._logger.debug(f"\t> Removing {q_next} from the queue. Total wait time was {q_next.get_wait_time()}")
        user_status = ""

        inperson = q_next.is_inperson()
        incall = in_voice_channel(q_next, channel, self._waiting_room.name)
        if inperson:
            user_status = "__*(in person)*__"
        elif self._config.CHECK_VOICE_WAITING:
            # TODO Use custom function for checking if user is in waiting room
            user_status = " (online and in voice)" if incall else " (online and **not** in voice)"
        await self._send(channel, f"""The next person is {q_next.get_mention()}{user_status}\nRemaining people in the queue: {len(queue)}""")

        if not inperson:
            if not incall:
                await self._send(channel, f"""Cannot automatically move student because they are not in voice""")
                return True

            # move them into the new vc
            user_to_move = channel.guild.get_member(q_next.get_uuid())
            ta_member = channel.guild.get_member(user.get_uuid())

            # check if TA is in vc
            if ta_member.voice is None:
                await self._send(channel, f"""Cannot automatically move student because {ta_member.mention} is not in voice.""")
                return True

            voice_channel = ta_member.voice.channel
            if voice_channel is None:
                return True

            await user_to_move.move_to(voice_channel)

        return True

    async def _q_add_other(self, user, mentions, channel, in_person=False):
        """
        Run when a TA calls "!q add @user". It will add the specified user
        to the queue if they are not already in there. A user can only give one
        user at a time (discord's API does not maintain mention order)
        *Must be run by a TA*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            mentions: list of mentions from the message object
            channel: discord.py channel object to send message to
            in_person: If true, set's user state to in-person otherwise set to online

        Returns: True if queue is updated; False otherwise
        """
        # Make sure mentions contains only one user
        if len(mentions) != 1:
            await self._send(channel, f"{user.get_mention()} invalid syntax. You must mention the user to add", CmdPrefix.ERROR)
            return False

        author = mentions[0]
        q_user = DiscordUser(author.id, author.name, author.discriminator, author.nick)
        q_user.set_inperson(in_person)
        queue = self.get_queue(channel)

        if q_user in queue:
            index = queue.index(q_user)
            await self._send(channel, f"{user.get_mention()} That person is already in the queue at position #{index}", CmdPrefix.WARNING)
            return False
        else:
            queue.append(q_user)
            self._join_times[q_user.get_uuid()] = datetime.now()

            await self._send(channel, f"{user.get_mention()} the person has been added at position #{len(queue)}", CmdPrefix.SUCCESS)
            return True

    async def _q_remove_other(self, user, mentions, channel):
        """
        Run when a TA calls "!q remove @user". It will remove the specified user
        to the queue if they are in the queue. This command can only add one
        user at a time (discord.py does not maintain mention order)
        *Does not check if user is a TA*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            mentions: list of mentions from the message object
            channel: discord.py channel object to send message to

        Returns: True if queue is updated; False otherwise
        """

        if len(mentions) != 1:
            await self._send(channel, f"{user.get_mention()} invalid syntax. You must mention the user to remove", CmdPrefix.ERROR)
            return False

        author = mentions[0]
        q_user = DiscordUser(author.id, author.name, author.discriminator, author.nick)
        queue = self.get_queue(channel)
        # TODO Test removing a user from the beginning of the queue

        if q_user in queue:
            queue.remove(q_user)
            await self._send(channel, f"{q_user.get_name()} has been removed from the queue", CmdPrefix.SUCCESS)
            await log_session(q_user.get_name(), self._join_times.get(q_user.get_uuid(), None), user.get_name(), "remove", channel.guild.name)
            return True
        else:
            await self._send(channel, f"{q_user.get_name()} is not in the queue", CmdPrefix.WARNING)
            return False

    async def _q_move_front_other(self, user, mentions, channel):
        """
        Run when a TA calls "!q front @user". It will add the specified user
        to the front of the queue. This command can only add one
        user at a time (discord.py does not maintain mention order)
        *Does not check if user is a TA*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            mentions: list of mentions from the message object
            channel: discord.py channel object to send message to

        Returns: True if queue is updated; False otherwise
        """
        if len(mentions) != 1:
            await self._send(channel, f"{user.get_mention()} invalid syntax. You must mention the user to remove", CmdPrefix.ERROR)
            return False
        else:
            author = mentions[0]
            q_user = DiscordUser(author.id, author.name, author.discriminator, author.nick)
            queue = self.get_queue(channel)

            if q_user in queue:
                queue.remove(q_user)
            queue.appendleft(q_user)
            # in this situation we do not want to change the join_time since they were already in the queue

            await self._send(channel, f"{q_user.get_name()} has been moved to the front of the queue", CmdPrefix.SUCCESS)
            return True

    async def _q_list(self, user, channel):
        """
        When a user runs "!q list" it will send a discord embed containing the next
        10 people within the list (people past 10 are not shown)
        *Can be run by anyone*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            mentions: list of mentions from the message object
            channel: discord.py channel object to send message to

        Returns: False (doesn't update queue)
        """
        # List the next 10 people within the queue in a nice formatted box (embed)
        user_list = []
        queue = self.get_queue(channel)
        queue_length = len(queue)

        # Get list of first 10 people in queue
        for i in range(0, min(10, queue_length)):
            user: DiscordUser = queue[i]
            user_metadata = ""
            if user.is_inperson():
                user_metadata = " *__(in person)__*"
            elif self._config.CHECK_VOICE_WAITING:
                #                Bold *
                user_metadata = " ** * **" if not in_voice_channel(user, channel, self._waiting_room.name) else ""

            user_list.append(f"**{i+1}.** {user.get_mention()}{user_metadata}")

        if queue_length >= 11:
            user_list.append(f"\n{queue_length-10} other(s) not shown")

        if self._config.CHECK_VOICE_WAITING and queue_length > 0:
            user_list.append("\n** * ** = user not in voice channel")

        description = f"Total in queue: {queue_length}" if queue_length else "Queue is empty"

        embed = discord.Embed(title=f"Queue List", description=description)
        if queue_length > 0:
            embed.add_field(name="Next 10 people:", value="\n".join(user_list), inline=False)
        await self._send(channel, embed=embed)
        return False

    async def _q_clear(self, user, channel):
        """
        Asks a confirmation message asking if the user wants to clear the queue
        *Must be run by a TA*

        Parameters:
            user: DiscordUser object representing the user who ran the command
            channel: discord.py channel object to send message to

        Returns: True if queue cleared; False otherwise
        """
        def check(reaction, user):
            # Couldn't get self.is_ta() working since it was an asynchronous routine
            has_ta_role = False
            for r in user.roles:
                if r.name in self._config.TA_ROLES:
                    has_ta_role = True
                    break

            if user == self.user or not has_ta_role:
                return False
            if str(reaction.emoji) == '✅':
                return True

            raise asyncio.TimeoutError()

        queue = self.get_queue(channel)

        if len(queue) == 0:
            await self._send(channel, "Queue is already empty")
            return False

        if self._testing:
            print("In testing mode; Skipping confirmation message")
            queue.clear()
            return True

        # TODO Convert message to constant
        message = await self._send(channel, constants.MSG_QUEUE_CLEAR)

        await message.add_reaction("✅")
        await message.add_reaction("❌")
        try:
            _, user = await self.wait_for('reaction_add', timeout=60.0, check=check)
        except asyncio.TimeoutError:
            await message.edit(content="Clearing queue canceled")
            return False
        else:
            self._logger.info(f"Emptying queue as per {user}'s request...")
            self._logger.debug("Queue prior to clearing: " +
                              ", ".join(str(el) for el in queue))

            for q_user in queue:
                await log_session(q_user.get_name(), self._join_times[q_user.get_uuid()], user.display_name, "clear", channel.guild.name)
                self._join_times[q_user.get_uuid()] = None

            queue.clear()

            await message.edit(content="Queue has been emptied")
            return True

    async def _q_logs(self, user, channel):
        discord_user = self.get_user(user.get_uuid())
        self._logger.info("\t> Sent logs to " + user.get_name())

        await self._send_dm(discord_user, None, log_message=False, file=f"logs/OH_logs_{channel.guild.name}.csv")
        await self._send(channel, f"{user.get_mention()} QueueBot logs have been to your Direct Messages", CmdPrefix.SUCCESS)
        return False


def get_user(channel, uuid):
    return channel.guild.get_member(uuid)


def in_voice_channel(user: DiscordUser, message_channel, channel_name):
    voice = get_user(message_channel, user.get_uuid()).voice
    if voice is None:
        return False
    return voice.channel.name == channel_name


def setup_loggers():
    """
    Save logs of what QueueBot and discord.py do
    https://docs.python.org/3/howto/logging.html

    Returns: QueueBot's logger
    """
    if not os.path.exists("logs"):
        os.mkdir("logs")

    discord_logger = logging.getLogger("discord")
    discord_logger.setLevel(logging.WARNING)
    queue_logger = logging.getLogger("queuebot")
    queue_logger.setLevel(logging.DEBUG)

    # discord.py file logging
    d_filehandler = logging.handlers.RotatingFileHandler(filename="logs/discord.log", encoding="utf-8", maxBytes=1000000, backupCount=5)
    d_filehandler.setLevel(logging.INFO)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s')
    d_filehandler.setFormatter(formatter)
    discord_logger.addHandler(d_filehandler)

    # queuebot.py console logging
    console = logging.StreamHandler(sys.stdout)
    formater = logging.Formatter('[%(asctime)s] [%(levelname)-8s] %(message)s',
                                 datefmt="%Y-%m-%d %H:%M:%S")
    console.setFormatter(formater)
    queue_logger.addHandler(console)

    # queuebot.py file logging
    q_filehandler = logging.handlers.RotatingFileHandler(filename="logs/queuebot.log", encoding="utf-8", maxBytes=1000000, backupCount=5)
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s')
    q_filehandler.setFormatter(formatter)
    queue_logger.addHandler(q_filehandler)

    return queue_logger


def main():
    queue_logger = setup_loggers()
    config = QueueConfig(get_config_json())
    queue_logger.info(f"Config:\n{config}")

    # Run Bot
    client = QueueBot(config, queue_logger)

    # TODO Catch KeyboardInterrupt and gracefully shut down bot
    client.run(config.SECRET_TOKEN)


if __name__ == "__main__":
    main()
