import asyncio
import json
import websockets
from blessed import Terminal
from typing import Dict, List
from dotenv import load_dotenv
import os
import sys

load_dotenv()

SERVER_URI=os.getenv("SERVER_URI", None)

if SERVER_URI is None:
    print("Please set the SERVER_URI environment variable.")
    sys.exit(1)

if not SERVER_URI.startswith("ws://"):
    print("Invalid SERVER_URI. It should start with 'ws://'.")
    sys.exit(1)

BLOCK = "█"
HORIZONTAL_WALL = "══"
TOP_LEFT_CORNER = "╔═"
TOP_RIGHT_CORNER = "═╗"
VERTICAL_WALL = "║"
BOTTOM_LEFT_CORNER = "╚═"
BOTTOM_RIGHT_CORNER = "═╝"

class Pos:
    def __init__(self, x, y):
        self.x = x
        self.y = y

class Tile:
    def __init__(self, symbol, walkable, pos: Pos, term: Terminal):
        self.symbol = symbol
        self.walkable = walkable
        self.pos = pos
        self.term = term
        print(self, end="", flush=True)

    def _render(self, color=None, custom_symbol=None):
        color = color or self.term.white
        custom_symbol = custom_symbol or self.symbol
        return self.term.move_xy(self.pos.x * 2, self.pos.y) + color(custom_symbol) + self.term.normal

    def __str__(self):
        return self._render()

class Wall(Tile):
    def __init__(self, pos, term: Terminal):
        super().__init__("▒▒", False, pos, term)

    def __str__(self):
        return self._render(color=self.term.grey37)

class Empty(Tile):
    def __init__(self, pos, term: Terminal):
        super().__init__("  ", True, pos, term)

    def __str__(self):
        return self._render(color=self.term.on_darkolivegreen)

class Player(Tile):
    def __init__(self, pos, term: Terminal, is_self=False):
        self.is_self = is_self
        super().__init__(BLOCK * 2, True, pos, term)

    def __str__(self):
        color = self.term.cyan2 if self.is_self else self.term.orange4
        return self._render(color=color)

    async def move(self, dx, dy, map: List[List[Tile]], ws: websockets.ClientConnection):
        new_x, new_y = self.pos.x + dx, self.pos.y + dy
        tile = map[new_y][new_x]
        if tile.walkable:
            await ws.send(json.dumps({"type": "move", "dir": "up" if dy < 0 else "down" if dy > 0 else "left" if dx < 0 else "right"}))



class Enemy(Tile):
    def __init__(self, pos, term: Terminal):
        super().__init__(BLOCK * 2, True, pos, term)

    def __str__(self):
            return self._render(color=self.term.red)


class Treasure(Tile):
    def __init__(self, pos, term: Terminal, collected=False):
        self.collected = collected
        super().__init__(BLOCK * 2, True, pos, term)

    def __str__(self):
        return self._render(color=self.term.gold if not self.collected else self.term.gray)
class Exit(Tile):
    def __init__(self, pos, term: Terminal):
        super().__init__("XX", True, pos, term)


class GameClient:
    def __init__(self, uri):
        self.uri = uri
        self.term = Terminal()
        self.map: List[List[Tile]] = []
        self.players: Dict[str, Player] = {}
        self.enemies: Dict[str, Enemy] = {}
        self.treasure = None
        self.exit = None
        self.map_size = (0, 0)
        self.player: Player | None = None
        self.player_id = None

    async def connect(self):
        async with websockets.connect(self.uri) as ws:
            await self.main_loop(ws)

    async def main_loop(self, ws: websockets.ClientConnection):
        print(self.term.home + self.term.clear)
        with self.term.fullscreen(), self.term.hidden_cursor(), self.term.cbreak():
            while True:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=0.05)
                    data = json.loads(message)
                    if data["type"] == "init":
                        self.handle_init(data)
                    elif data["type"] == "state":
                        self.handle_state(data)
                except asyncio.TimeoutError:
                    pass

                key = self.term.inkey(timeout=0.01)
                if key.lower() == "q":
                    await ws.close()
                    sys.exit(0)
                if key and self.player:
                    if key.lower() == "w":
                        await self.player.move(0, -1, self.map, ws)
                    elif key.lower() == "s":
                        await self.player.move(0, 1, self.map, ws)
                    elif key.lower() == "a":
                        await self.player.move(-1, 0, self.map, ws)
                    elif key.lower() == "d":
                        await self.player.move(1, 0, self.map, ws)

    def handle_init(self, data):
        self.map_size = (data["width"], data["height"])
        self.map = [[Empty(Pos(x, y), self.term) for x in range(data["width"])] for y in range(data["height"])]
        for wall in data["walls"]:
            x, y = wall["x"], wall["y"]
            self.map[y][x] = Wall(Pos(x, y), self.term)
        if "exit" in data:
            x, y = data["exit"]["x"], data["exit"]["y"]
            self.exit = Exit(Pos(x, y), self.term)
            self.map[y][x] = self.exit

    def handle_state(self, data):
        self.player_id = data.get("you")

        for pid, pos in data["players"].items():
            if pid in self.players:
                player = self.players[pid]
                self.map[player.pos.y][player.pos.x] = Empty(player.pos, self.term)
                player.pos.x = pos["x"]
                player.pos.y = pos["y"]
                print(player, end="", flush=True)
            else:
                tile = Player(Pos(pos["x"], pos["y"]), self.term, is_self=(pid == self.player_id))
                self.players[pid] = tile
                self.map[pos["y"]][pos["x"]] = tile

        self.player = self.players.get(self.player_id)

        for eid, pos in data["enemies"].items():
            if eid in self.enemies:
                enemy = self.enemies[eid]
                self.map[enemy.pos.y][enemy.pos.x] = Empty(enemy.pos, self.term)
                enemy.pos.x = pos["x"]
                enemy.pos.y = pos["y"]
                print(enemy, end="", flush=True)
            else:
                enemy = Enemy(Pos(pos["x"], pos["y"]), self.term)
                self.enemies[eid] = enemy


        if "treasure" in data:
            t = data["treasure"]
            self.treasure = Treasure(Pos(t["x"], t["y"]), self.term, collected=t.get("collected", False))
            self.map[t["y"]][t["x"]] = self.treasure

if __name__ == "__main__":
    try:
        client = GameClient(SERVER_URI)
        asyncio.run(client.connect())
    except KeyboardInterrupt:
        print("Disconnected.")
