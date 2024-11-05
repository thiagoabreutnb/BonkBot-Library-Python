#!/usr/bin/env python3
import json
import requests as rq
import time
import warnings
import websockets
import websockets.legacy.client
import ssl
import asyncio
import re

insecure_ssl = ssl._create_unverified_context()

warnings.filterwarnings("ignore", category=rq.urllib3.exceptions.InsecureRequestWarning)

class utils:
    def address_from_url(url):
        page = rq.get(url)
        data = re.findall('contentWindow\\.autoJoin\\ =\\ \\{"address":"(.*?)","roomname":".*?","server":"(.*?)","passbypass":"(.*?)","r":"success"\\}',
                          page.text)[0]
        return {
            "roomadd": data[0],
            "roomserver": data[1],
            "roombypass": data[2]
        }

    def address_from_roomid(roomid):
        roomadd_rq = rq.post("https://bonk2.io/scripts/getroomaddress.php", data=f"id={roomid}", headers={"content-type":"application/x-www-form-urlencoded; charset=UTF-8"}, verify=False).json()
        return {
            "roomadd": roomadd_rq["address"],
            "roomserver": roomadd_rq["server"],
            "roombypass": "nmaac"
        }
    
    def get_peerid(roomserver):
        return rq.get(f"https://{roomserver}.bonk.io/myapp/peerjs/id", verify=False).text

    yeast_alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"

    def yeast():
        num = int(time.time()*1000)
        encoded = ""
        while num > 0 or not encoded:
            encoded = utils.yeast_alphabet[num % len(utils.yeast_alphabet)] + encoded
            num //= len(utils.yeast_alphabet)
        return encoded

    def get_sid(roomserver):
        sid_rq = rq.get(f"https://{roomserver}.bonk.io/socket.io/?EIO=3&transport=polling&t={utils.yeast()}", verify=False)
          # This line prints the data
        try:
            return json.loads(sid_rq.text[sid_rq.text.index("{"):])["sid"]
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
            # Handle the error (maybe try again or log it)

    def login(username, password):
        login_rq = rq.post("https://bonk2.io/scripts/login_legacy.php", data=f"username={username}&password={password}&remember=false", headers={"content-type":"application/x-www-form-urlencoded; charset=UTF-8"}, verify=False)
        login_json = login_rq.json()
        if login_json["r"] != "success":
            raise ValueError("Login API returned with error: " + repr(login_json["e"]))
        return login_json

    def get_ws(roomserver, sid):
        return websockets.connect(f"wss://{roomserver}.bonk.io/socket.io/?EIO=3&transport=websocket&sid={sid}", ssl=insecure_ssl)

    def get_create_server():
        rooms_rq = rq.post("https://bonk2.io/scripts/getrooms.php", data="version=44&gl=y&token=", headers={"content-type":"application/x-www-form-urlencoded; charset=UTF-8"}, verify=False)
        rooms_json = rooms_rq.json()
        server = rooms_json["createserver"]
        if not server:
            raise ValueError("bonk.io gave no create server.")
        return server

        
        

class BonkNetworkInterface:
    ws: websockets.legacy.client.WebSocketClientProtocol
    
    def __init__(self, ws, peerid, roomadd=None, roombypass="nmaac"):
        self.ws = ws
        self.peerid, self.roomadd, self.roombypass = peerid, roomadd, roombypass
        self.last_beat = time.time()

    async def initialize(self):
        await self.ws.send("2probe")
        assert ("3probe" == await self.ws.recv()), "Did not get response '3probe'"
        await self.ws.send("5")
        assert ("40" == await self.ws.recv()), "Did not get response '40'"

    async def heartbeat_if_needed(self):
        if time.time() - self.last_beat > 10:
            await self.ws.send("2")
##            print("\033[94;1mSEND:\033[0;35m + Heartbeat\033[0m")
            self.last_beat += 10

    def send_raw(self, data):
         return self.ws.send(data)

    def send_json(self, obj):
        return self.ws.send("42" + json.dumps(obj, separators=(",",":")))

    def send_chat(self, msg):
        return self.send_json([10, {"message":str(msg)}])

    def send_join_room(self, *, guest_name="UnnamedBot", token=None, roompass="", avatar={"layers":[],"bc":0}):
        """
        Provide no token to join as a guest.
        """
        if self.roomadd == None:
            raise AttributeError("No roomadd given. You can only create a room without a room address.")
        guest = not token
        return self.send_json([13, {
            "joinID": self.roomadd,
            "avatar": avatar,
            "roomPassword": roompass,
            "guest": guest,
            "token": token,
            "guestName": guest_name,
            "peerID": self.peerid,
            "bypass": self.roombypass,
            "dbid": 2,
            "version": 44
        }])

    def send_create_room(self, room_name, max_players=8, roompass="", unlisted=False, *, latlon=(0, 0), country="US", min_level=0, max_level=999, guest_name="UnnamedBot", token=None, avatar={"layers":[],"bc":0}):
        """
        Provide no token to create as a guest.
        """
        guest = not token
        return self.send_json([12, {
            "roomName": room_name,
            "maxPlayers": max_players,
            "minLevel": min_level,
            "maxLevel": max_level,
            "latitude": latlon[0],
            "longitude": latlon[1],
            "country": country,
            "hidden": int(unlisted),
            'quick': False,  # Could support making quickplay rooms in future
            'mode': "custom",
##            'quick': True,  # Could support making quickplay rooms in future
##            'mode': "grapplequick",
            'token': token,
            "avatar": avatar,
            "password": roompass,
            "guest": guest,
            "guestName": guest_name,
            "peerID": self.peerid,
            "dbid": 11631043,
            "version": 44
        }])

class BonkSession:
    ws: BonkNetworkInterface
    def __init__(self, ws, peerid, roomadd=None, roombypass="nmaac"):
        self.players = []
        self.ws = BonkNetworkInterface(ws, peerid, roomadd, roombypass)
        self.sharelink = ""

    async def get_msg(self):
        try:
            msg = await asyncio.wait_for(self.ws.ws.recv(), 0.1)
            if type(msg) is not str: return None   
    
            if msg.startswith("42"):
               msg = json.loads(msg[2:])
            if msg[0] == 49:   
                self.sharelink = f"https://bonk.io/{msg[1]:0>6}{msg[2]}"
            if msg[0] == 3:
                self.sharelink = f"https://bonk.io/{msg[6]:0>6}{msg[7]}"
            return msg
        except asyncio.exceptions.TimeoutError:
            return None

TEAMNAMES = ["spectator", "ffa", "red", "blue", "green", "yellow"]
def print_pack(data, playernames=[]):
    def name(id):
        if id in playernames:
            return f"{playernames[id]} [{id}]"
        else:
            return f"[id {id}]"
    #print(text)
    if data[0] != 7 and data != "3":
        print("\033[33;1mRECV: \033[0;32m", end="")
    if type(data) is list:
        if data[0] == 1:
            print("* Ping")
        elif data[0] == 2:
            print(f"* Room created! The room address is {data[1]}. \033[0mExtra data (usually [1, None]): {data[2:]}")
        # Packet 2: On create room, extra info
        elif data[0] == 3:
            print(f"* Initial data:\n\
My ID: {data[1]}\n\
Host ID: {data[2]} ({data[3][data[2]]['userName']})\n\
Players here:")
            for i, p in enumerate(data[3]):
                if p is not None:
                    print(f"  ID {i}: '{p['userName']}'")
            print(f"Server Unix time: {data[4]}")
            print(f"Teams are {'' if data[5] else 'un'}locked")
            print(f"Share URL: https://bonk.io/{data[6]:0>6}{data[7]}")
            print(f"Unknown data: \033[0m{data[8:]}")
            # unk data: [team lock, room id, room bypass, ?]
##            print("\033[0m", data)
        elif data[0] == 4:
            print(f"* [Player {data[1]}] named \"{data[3]}\" joined the game. They are a {'guest' if data[4] else 'registered user'}")
        elif data[0] == 5:
            print(f"* {name(data[1])} left the game. \033[0mExtra data (time?): {repr(data[2])}")
        elif data[0] == 6:
            # [6, 0, 1, 49364720167]
            if data[2] == -1:
                print(f"* Host {name(data[1])} left game and closed the room. \033[0mExtra data (time*30?): {data[3]}")
            else:
                print(f"* Host {name(data[1])} left game and gave host to {name(data[2])}. \033[0mExtra data (time*30?): {data[3]}")
        elif data[0] == 7:
            pass
 #            print(f"* [Player {data[1]}] changed movement: left={'y' if data[2]['i'] & 1 else 'n'} right={'y' if data[2]['i'] & 2 else 'n'}\
 # up={'y' if data[2]['i'] & 4 else 'n'} down={'y' if data[2]['i'] & 8 else 'n'} heavy={'y' if data[2]['i'] & 16 else 'n'} special={'y' if data[2]['i'] & 32 else 'n'}")
        elif data[0] == 8:
            print(f"* {name(data[1])} turned [READY] {'on' if data[2] else 'off'}")
        elif data[0] == 13:
            print("* Game end")
        elif data[0] == 12:
            print(f"* {name(data[1])}'s name was changed to {data[2]}")
        elif data[0] == 15:
            print("* Game start")
##            print("\033[0m", data)
        elif data[0] == 16:
            print(f"\033[31m* {data[1].replace('_', ' ')}")
        elif data[0] == 18:
            print(f"* {name(data[1])} moved to team {TEAMNAMES[data[2]]}")
        elif data[0] == 19:
            print(f"* Teams {'' if data[1] else 'un'}locked")
        elif data[0] == 20:
            print(f"* {name(data[1])}: {repr(data[2])}")
        elif data[0] == 21:
            print("* Initial map data")
        elif data[0] == 24:
            print(f"* {name(data[1])} kicked")
##            print("\033[0m", data)
        elif data[0] == 26:
            known_engines = {
                "f": "Football",
                "b": "Bonk"
            }
            known_modes = {
                "f": "Football",
                "bs": "Simple",
                "ard": "Death Arrows",
                "ar": "Arrows",
                "sp": "Grapple",
                "v": "VTOL",
                "b": "Classic"
            }
            engine = (f"{known_engines[data[1]]} [{data[1]}]") if data[1] in known_engines else data[1]
            mode = (f"{known_modes[data[2]]} [{data[2]}]") if data[2] in known_modes else data[2]
            print(f"* Game mode changed, engine {engine} with mode {mode}")
        elif data[0] == 27:
            print(f"* Rounds set to {data[1]}")
        elif data[0] == 29:
            if data[1].startswith("!!!GMMODE!!!"):
                print("* GMM mode switch")
            else:
                print("* Map switch")
##            print("\033[0m", data)
        elif data[0] == 32:
            # Only in Quickplay
            print("* About to be kicked for inactivity!")
        elif data[0] == 34:
            print(f"* {name(data[3])} suggests '{data[1]}' by '{data[2]}'")
        elif data[0] == 36:
            print(f"* {name(data[1])}'s balance set to {data[2]}%")
        elif data[0] == 39:
            print(f"* Teams turned {'on' if data[1] else 'off'}")
        elif data[0] == 41:
            print(f"* {name(data[1]['oldHost'])} gave {name(data[1]['newHost'])} host")
        elif data[0] == 42:
            print(f"* Friend request from {name(data[1])}")
        elif data[0] == 43:
            print(f"* Game starting in {data[1]}")
        elif data[0] == 44:
            print("* Game start cancelled")
        elif data[0] == 45:
            print(f"* {name(data[1]['sid'])} leveled up to Level {data[1]['lv']}")
        elif data[0] == 46:
            if data[1].get("newLevel"):
                print(f"* Got XP, now at {data[1]['newXP']} XP and level {data[1]['newLevel']}")
                print(data)
            else:
                print(f"* Got XP, now at {data[1]['newXP']} XP")
        elif data[0] == 47:
            print(f"* Lagged, ignore your inputs before and on frame {data[1]}")
        elif data[0] == 48:
            print("* Initial map data (ingame)")
        elif data[0] == 49:
            print(f"* Share link is https://bonk.io/{data[1]:0>6}{data[2]}")
        # Packet 2: On create room, get share link info
        elif data[0] == 52:
            print(f"* {name(data[1])} tabbed {'out' if data[2] else 'in'}")
        else:
            print("\033[0m", data)
        print("\033[0m", end="")
##        return data
    elif data == "3":
        pass
##        print("+ Heartbeat")
    elif data == "41":
        print("+ Connection closed.")
    else:
        print("\033[0munknown", repr(data))
    print("\033[0m", end="")


