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
    def __init__(self, symbol: str, walkable: bool, pos: Pos, term: Terminal):
        self.symbol = symbol
        self.walkable = walkable
        self.pos = pos
        self.term = term

    def _render(self, color=None, custom_symbol=None):
        color = color or self.term.white
        custom_symbol = custom_symbol or self.symbol
        return self.term.move_xy(self.pos.x * 2, self.pos.y) + color(custom_symbol) + self.term.normal

    def __str__(self):
        return self._render()


class Wall(Tile):

    def __init__(self, pos, term: Terminal, map_size: tuple[int, int], is_pretty=False):
        self.map_size = map_size
        self.is_pretty = is_pretty
        super().__init__("▒▒", False, pos, term)

    def _render(self, color=None, custom_symbol=None):
        return super()._render(color=self.term.ivory4, custom_symbol=custom_symbol)

    def __str__(self):
        if not self.is_pretty:
            if self.pos.y == 0 or self.pos.y == self.map_size[1] - 1:
                return self._render(custom_symbol=BLOCK*2)
            if self.pos.x == 0 or self.pos.x == self.map_size[0] - 1:
                return self._render(custom_symbol=BLOCK*2)
        if self.pos.x == 0 and self.pos.y == 0:
            return self._render(custom_symbol=TOP_LEFT_CORNER)
        if self.pos.x == self.map_size[0] - 1 and self.pos.y == 0:
            return self._render(custom_symbol=TOP_RIGHT_CORNER)
        if self.pos.x == 0 and self.pos.y == self.map_size[1] - 1:
            return self._render(custom_symbol=BOTTOM_LEFT_CORNER)
        if self.pos.x == self.map_size[0] - 1 and self.pos.y == self.map_size[1] - 1:
            return self._render(custom_symbol=BOTTOM_RIGHT_CORNER)
        if self.pos.x == 0:
            return self._render(custom_symbol=VERTICAL_WALL)
        if self.pos.x == self.map_size[0] - 1:
            return self._render(custom_symbol=" " + VERTICAL_WALL)
        if self.pos.y == 0 or self.pos.y == self.map_size[1] - 1:
            return self._render(custom_symbol=HORIZONTAL_WALL)

        return super().__str__()


class Empty(Tile):
    def __init__(self, pos, term: Terminal):
        super().__init__("  ", True, pos, term)

    def __str__(self):
        return self._render(color=self.term.on_darkolivegreen)


class Player(Tile):
    def __init__(self, pos, term: Terminal, is_self=False):
        super().__init__(BLOCK * 2, True, pos, term)
        self.is_self = is_self

    def __str__(self):
        return self._render(color=self.term.cyan2 if self.is_self else self.term.orange4)


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
        self.map = []
        self.players: Dict[str, Player] = {}
        self.enemies: List[Enemy] = []
        self.treasure: Treasure | None = None
        self.exit: Exit | None = None
        self.map_size = (0, 0)
        self.player_id = None

    async def connect(self):
        async with websockets.connect(self.uri) as websocket:
            print("Connected to server.")
            await self.main_loop(websocket)

    async def main_loop(self, ws):
        with self.term.hidden_cursor(), self.term.cbreak():
            while True:
                message = await ws.recv()
                data = json.loads(message)

                if data["type"] == "init":
                    self.handle_init(data)
                elif data["type"] == "state":
                    self.handle_state(data)
                self.draw()

                key = self.term.inkey(timeout=0.05)
                if key.lower() == "q":
                    exit()
                if key:
                    direction = {"w": "up", "s": "down", "a": "left", "d": "right"}.get(key.lower())
                    print(direction)
                    if direction:
                        await ws.send(json.dumps({"type": "move", "dir": direction}))

    def handle_init(self, data):
        self.map_size = (data["width"], data["height"])
        self.map = [[Empty(Pos(x, y), self.term) for x in range(data["width"])] for y in range(data["height"])]
        for wall in data["walls"]:
            x, y = wall["x"], wall["y"]
            self.map[y][x] = Wall(Pos(x, y), self.term, self.map_size)
        if "exit" in data:
            x, y = data["exit"]["x"], data["exit"]["y"]
            self.exit = Exit(Pos(x, y), self.term)
            self.map[y][x] = self.exit

    def handle_state(self, data):
        self.players.clear()
        self.enemies.clear()

        for pid, pos in data["players"].items():
            player = Player(Pos(pos["x"], pos["y"]), self.term, is_self=(pid == data.get("you")))
            self.players[pid] = player
            self.map[pos["y"]][pos["x"]] = player

        for pos in data["enemies"]:
            enemy = Enemy(Pos(pos["x"], pos["y"]), self.term)
            self.enemies.append(enemy)
            self.map[pos["y"]][pos["x"]] = enemy

        if "treasure" in data:
            t = data["treasure"]
            self.treasure = Treasure(Pos(t["x"], t["y"]), self.term, collected=t.get("collected", False))
            self.map[t["y"]][t["x"]] = self.treasure

    def draw(self):
        print(self.term.home + self.term.clear)
        for row in self.map:
            for tile in row:
                print(tile, end="")
            print()


if __name__ == "__main__":
    try:
        client = GameClient(SERVER_URI)
        asyncio.run(client.connect())
    except KeyboardInterrupt:
        print("Disconnected.")
