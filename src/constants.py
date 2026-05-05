
MSG_HELP = {
            "STUDENT": """__STUDENT COMMANDS:__
> `!q help` - Get this help message
> `!q join`  - Join the queue (ONLINE aka TA will assist you via Discord screen share)
> `!q join-inperson`  - Join the queue (IN-PERSON aka TA will assist you on your own computer)
> `!q leave` - Leave the queue
> `!q position` - See how many people are in front of you
> `!q list` - Get a list of the next 10 people in line""",

            "TA": """__TA COMMANDS:__
> `!q help` - Get this help message
> `!q next` - Get the next person within that class to help **(REMOVES FROM QUEUE)**
> `!q clear` - Empty the queue (requires confirmation)
> `!q list` - Get a list of the next 10 people in line
> `!q ping` - Bot should reply with `Pong!` Used to make sure bot can send/receive messages
> `!q add @user` - add @user to the end of the queue and marks them as online (you must @mention the person)
> `!q add-inperson @user` - add @user to the end of the queue and marks them as in-person (you must @mention the person)
> `!q remove @user` - remove @user from the queue (you must @mention the person)
> `!q front @user` - adds/moves @user to the front of the queue (you must @mention the person)
> `!q logs` - Get logs of office hours as a file in DMs
NOTE: TAs can also run student commands""",
}

MSG_QUEUE_CLEAR = """Are you sure you want to clear the queue?
React with ✅ to confirm or ❌ to cancel"""