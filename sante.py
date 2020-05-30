"""This program verifies the messages of the forum, with the "twinoïd page"
https://twinoid.com/g/gestion-pseudo-publique#donnees-pour-tourner-le-code as a starting point.
Then

a) if a "complete" message was already posted today :
1) output the "cleaned" message to be posted on the forum
2) output the players to be contacted, as well as the messages to be sent to them

b) else :
1) output the new "complete" message to be posted on the forum
2) verifies that the factor of 1.1 is respected
3) verifies that the ages of death are respected
"""

import re
import sys
import traceback
import urllib.request
import time
from math import ceil
import matplotlib.pyplot as plt
import numpy as np
import datetime
import logging
import dateparser

logging.getLogger().setLevel(logging.DEBUG)

# constants

SID = "lrFEChtfKbKVTZYyn89wvpgdpEEyIDGH"
# used in the urls of muxxu, designating the computer (?) using it. Not sure about this one.

FORUM_ADDRESS = ("https://twinoid.com/mod/forum/thread/{{}}?p={{}};_id=tid_forum;jsm=1;lang=fr;"
                 "host=kingdom.muxxu.com;proto=http%3A;sid={}".format(SID))
INTRO = "Liste des joueurs:<br/>"
ENDING = "<br/>Programme tourné le: "
SANTE = ["né le <date>", "1er comptage", *["{}ème comptage".format(i) for i in range(2, 9)],
         "Excellente santé", "Bonne santé", "Mauvaise santé", "Mort à venir", "Mort"]


# classes

class MessageExcept:
    """ Messages of the forum that does NOT have to be processed (because wrong or whatever).
    These exceptions are reported in the twinoïd page, in the form :
    except : thread <thread number (see URL)> page <page number> message <position in terms of content.split(INTRO)[1:]>
    Can be initialised through "s" (following the above format) or by specifying each element of it.
    """
    def __init__(self, thread=None, page=None, position=None, s=None):
        if s is not None:
            datas = re.search('except : thread (\d+) page (\d+) message (\d+)', s)
            self.thread = int(datas.group(1))
            self.page = int(datas.group(2))
            self.position = int(datas.group(3))
        else:
            self.thread = thread
            self.page = page
            self.position = position

    def __eq__(self, other):
        """To be able to do e.g. "(<thread>, <page>, <pos>) in <list of MessageExcept>" """
        if isinstance(other, MessageExcept):
            thread, page, position = other.thread, other.page, other.position
        elif isinstance(other, tuple):
            thread, page, position = other
        else:
            raise RuntimeError("Impossible comparison of MessageExcept with {}".format(type(other)))
        return self.thread == thread and self.page == page and self.position == position

    def __repr__(self):
        return "<MessageExcept: thread {}, page {}, position {}>".format(self.thread, self.page, self.position)


class ForumSource:
    """ Contains the raw code of a page of the forum, as well as the thread and page numbers it comes from."""
    def __init__(self, thread, page, content):
        self.thread = thread
        self.page = page
        self.content = content

    def __repr__(self):
        return "<ForumSource: thread {} page {}>".format(self.thread, self.page)


class RankingSource:
    """ Contains the raw code of a page of the rankings, as well as the map and page numbers it comes from."""
    def __init__(self, map_, page, content):
        self.map = map_
        self.page = page
        self.content = content

    def __repr__(self):
        return "<RankingSource: map {} page {}>".format(self.map, self.page)


class MuxxuGroup:
    """ Represents a muxxu group, with its name (self.group), map number and a (random) city on that map.
    These groups are reported in the twinoïd page, in the form :
    groupe muxxu : "<name>" ; carte : <map number> ; ville : <city number>
    Can be initialised through "s" (following the above format) or by specifying each element of it.
    """
    def __init__(self, group=None, map_=None, city=None, s=None):
        if s is not None:
            datas = re.search(r'groupe muxxu : &quot;(.*?)&quot; ; carte : (\d+) ; ville : (\d+)', s)
            self.group = datas.group(1)
            self.map = int(datas.group(2))
            self.city = int(datas.group(3))
        else:
            self.group = group
            self.map = int(map_)
            self.city = int(city)
        if not all([c.isalnum() or c in "-_" for c in self.group]):
            raise RuntimeError("Nom de groupe muxxu inattendu: {}.".format(self.group))

    def __repr__(self):
        return "<MuxxuGroup: group {}>".format(self.group)


class Player:
    """Represents a player, with all its states.
    Its muxxu_id is considered as unique and is therefore used as a bijection between ids and players.
    This class can be initialised thanks to the forum following the format
    <span class="user" tid_bg="1" tid_id="[twinoïd id]">[player name]</span>-[muxxu id]-[other stuffs]
    (here, '<' are real '<', therefore '[ ]' is used instead to indicate a replaceable element)
    Can be initialised through "s" (following the above format) or by specifying each element of it.
    Also has a dict of {<datetime>: <PlayerState>}, representing the player history.
    """

    def __init__(self, muxxu_id=None, twino_id=None, name=None, s=None):
        if s is None:
            self.muxxu_id = muxxu_id
            self.twino_id = twino_id
            self.name = name
        else:
            datas = re.search(r'<span class="user" tid_bg="1" tid_id="(\d+)">(.*?)</span>-(\d+)-', s)
            if not datas:
                raise ValueError("Input line is not in the expected format: {}".format(s))
            self.twino_id = int(datas.group(1))
            self.name = datas.group(2)
            self.muxxu_id = int(datas.group(3))
        self.states = {}

    def __eq__(self, other):
        """To be able to do "<muxxu_id> in <list of Player>" """
        if isinstance(other, Player):
            other = other.muxxu_id
        return self.muxxu_id == other

    def __repr__(self):
        return "<Player @{}:{} ({}) : {}>".format(self.name, self.twino_id, self.muxxu_id, repr(self.states))

    @property
    def last_born(self):
        """ Throw an error if no born. (shouldn't happen except at the beginning of the use of the program)"""
        return max([st.time for st in self.states.values() if st.health == 0])


class PlayerState:
    """ Represents a state of a player.
    This class can be initialised thanks to the forum following the format
    [other stuffs]-[years].[month] : [health] + (<span class="funTag funTag_dice100">[dice result]</span> &lt;= [threshold])
    or
    [other stuffs]-[years].[month] : [health] + 1
    or
    [other stuffs]-[years].[month] : [health]
    (depending on health value)
    Can be initialised through "s" (following the above format) or by specifying each element of it.
    Whichever is chosen, time has to be given separately.
    """
    def __init__(self, time=None, year=None, month=None, health=None, s=None):
        self.time = time
        if s is None:
            self.year = year
            self.month = month
            self.health = health
        else:  # read from forum
            datas = re.search(r'-(\d+)\.(\d+) : (.*?)(?: \+ \(<span class="funTag funTag_dice100">(\d+)</span> &lt;= (\d+)\))?(?: \+ 1)?$', s)
            if not datas:
                raise ValueError("Input line is not in the expected format: {}".format(s))
            self.year = int(datas.group(1))
            self.month = int(datas.group(2))
            if datas.group(3).startswith("né le "):
                self.health = 1
            else:
                self.health = new_health(SANTE.index(datas.group(3)),
                                         int(datas.group(4)) if datas.group(4) else None,
                                         int(datas.group(5)) if datas.group(5) else None)

    def __repr__(self):
        return "<PlayerState {}: {} à {}.{:02d}>".format(
            self.time, SANTE[self.health], self.year, self.month or 0)

    @property
    def age(self):
        return self.year + self.month / 12.0

    def __eq__(self, other):
        # if isinstance(other, datetime.datetime):
        #     return other.date() == self.time.date()
        raise NotImplementedError()


# Helpers

def between(before, s, after):
    return s.partition(before)[2].partition(after)[0]


def get_player_from_muxxu_id(muxxu_id):
    source = get_source_code('http://kingdom.muxxu.com/user/{}'.format(muxxu_id))
    datas = re.search('<div class="tid_user" tid_id="(\d+)" tid_bg="0">(.*?)</div>', source)
    player = Player(muxxu_id, int(datas.group(1)), datas.group(2))
    logging.debug("Searched muxxu player {}".format(player))
    return player


def get_source_code(url):
    """ Function to be used if a page is wanted, to be sure to wait 1 second each time."""
    time.sleep(1)
    return urllib.request.urlopen(url).read().decode("utf8")


def new_health(health, dice, threshold):
    if health < SANTE.index("Excellente santé"):
        return health + 1
    if health == len(SANTE)-1:
        raise RuntimeError("Pas de 'santé suivante' si mort...")
    if health == SANTE.index("Mort à venir"):
        return health
    if dice is None:
        raise RuntimeError("Dice should not be None")
    return health + (dice <= threshold)


def get_threshold(days):
    """ How the threshold is computed (may be modified)."""
    return 9 + 3 * (days // 10)


# main functions

def get_inputs():
    """Get inputs from the twinoïd page. (muxxu groups, thread of the forum and exceptions)"""
    muxxu_groups = []
    threads = []
    excepts = []

    source_code = get_source_code('https://twinoid.com/mod/group/10562/donnees-pour-tourner-le-code?'
                                  'jsm=1;host=twinoid.com;sid={}'.format(SID))
    for line_code in source_code.split("\n"):
        if '<div class="editorContent">' in line_code:
            data_string = between("<pre>", line_code, "</pre>")
            for data_line in data_string.split("\\n"):
                if data_line.startswith("groupe muxxu : "):
                    muxxu_groups.append(MuxxuGroup(s=data_line))
                elif data_line.startswith("thread : "):
                    threads.append(int(data_line[9:]))
                elif data_line.startswith("except : "):
                    excepts.append(MessageExcept(s=data_line))
            break
    else:
        raise RuntimeError("Input not found.")
    return muxxu_groups, threads, excepts


def get_from_forum(threads):
    """ Gets the source code of the forum pages. Returns a list of ForumSource objects."""
    forum_sources = []
    for thread in threads:
        for page in range(1, 101):
            logging.debug("extracting forum thread {} page {}".format(thread, page))
            source_code = get_source_code(FORUM_ADDRESS.format(thread, page))
            forum_sources.append(ForumSource(thread, page, source_code))
            total_page = re.search(r'<span class="pageTotal">/ (\d+)</span>',
                                   source_code.partition('<div class="buttonBar">')[0])
            if not total_page:  # Only one-page thread
                logging.debug("one-page thread {}".format(thread))
                break
            if page == int(total_page.group(1)):  # Last page found
                logging.debug("last page of thread {}: {}".format(thread, page))
                break
    return forum_sources


def read_forum_sources(forum_sources, excepts):
    """ Reads the forum to check the posted messages and extract players information.

    :param forum_sources: list of ForumSource objects, the forum to be analysed (has to be in chronological order)
    :param excepts: list of MessageExcept objects, parts of the forum to be ignored
    :return: dict of {<muxxu_id>: <Player>} with their states completed thanks to the information of the forum,
        as well as the time of the last message on the forum
    """
    # TODO: vérifier que tous les joueurs en vie apparaissent dans toutes les prises
    # (ou à faire dans la fonction "checks")
    players = {}
    last_date = datetime.datetime(2000, 1, 1)
    for forum_source in forum_sources:
        for i, message in enumerate(forum_source.content.split(INTRO)[1:]):
            message = message.partition("</div>")[0]
            if (forum_source.thread, forum_source.page, i) in excepts:
                logging.debug("Message skipped: {}".format(message.replace("\n", " ")))
                continue

            try:  # check message correctness
                message_time = datetime.datetime.strptime(message.partition(ENDING)[2][:19], "%d-%m-%Y %H:%M:%S")
                for player_line in message.partition(ENDING)[0].split("<br/>"):
                    p = Player(s=player_line)
                    p = players.get(p.muxxu_id, p)
                    ps = PlayerState(time=message_time, s=player_line)
                    search = re.search('<span class="funTag funTag_dice100">(\d+)</span> &lt;= (\d+)', player_line)
                    if search:
                        delta = (message_time.date() - players[p.muxxu_id].last_born.date()).days
                        assert int(search.group(2)) == get_threshold(delta), "Prévenir @simoons:528629 svp."
                    check = (ps.health == 1 or  # newborn, never there before on the forum
                             ps.health == new_health(p.states[max(p.states)].health,
                                                     None if search is None else int(search.group(1)),
                                                     None if search is None else int(search.group(2))))
                    assert check, "Prévenir @simoons:528629 svp."
                    born = re.search('né le (.*?)$', player_line)
                    if born:
                        datetime.datetime.strptime(born.group(1), "%d-%m-%Y %H:%M:%S")
            except Exception as e:
                traceback.print_exc(limit=3)
                logging.warning(repr(e))
                logging.warning("Veuillez bannir un message erroné (probablement "
                                "'except : thread {} page {} message {}'), "
                                "soit en contactant @simoons:528629 soit en l'ajoutant à la page "
                                "https://twinoid.com/g/gestion-pseudo-publique#donnees-pour-tourner-le-code ."
                                "".format(forum_source.thread, forum_source.page, i))
                continue

            # Update info if the message is ok
            for player_line in message.partition(ENDING)[0].split("<br/>"):
                player = Player(s=player_line)
                if player.muxxu_id in players:
                    player = players[player.muxxu_id]
                player.states[message_time] = PlayerState(time=message_time, s=player_line)
                born = re.search('né le (.*?)$', player_line)
                if born:
                    date_born = datetime.datetime.strptime(born.group(1), "%d-%m-%Y %H:%M:%S")
                    player.states[date_born] = PlayerState(date_born, 20, 0, 0)
                players[player.muxxu_id] = player
            last_date = max(message_time, last_date)
    return players, last_date


def get_map_histo(muxxu_groups, players):
    """ Updates players with the history of the maps (to get new newborns).
    To be run after the forum has been read (to avoid searching the twino_id of the players unnecessarily)."""
    for muxxu_group in muxxu_groups:
        source = get_source_code('http://kingdom.muxxu.com/map?c={}'.format(muxxu_group.city))
        histo = between('<div class="log">', source, "</div>")
        for entry in histo.split('</li><li>')[::-1]:  # [::-1] to get it in chronological order
            if not '<img src="/img/icons/l_new.png"/>' in entry:  # search only birth
                continue
            date = dateparser.parse(between('<span class="datelog">', entry, '</span>'))
            datas = re.search(r'<a href="/user/(\d+)">', entry)
            muxxu_id = int(datas.group(1))
            if muxxu_id not in players:
                players[muxxu_id] = get_player_from_muxxu_id(muxxu_id)
            player = players[muxxu_id]
            if date in player.states:
                if player.states[date].health != 0:  # TODO: Might be a big problem :D.
                    raise RuntimeError("Deux états à la même seconde, contacte @simoons:528629 pour régler ça stp.")
                continue
            player.states[date] = PlayerState(date, 20, 0, 0)


def get_rankings(muxxu_groups):
    """ Gets the ranking sources, returning an array of RankingSource objects,
    as well as the datetime "now", the moment they got taken."""
    # TODO: sometimes, rankings go wrong, with some players being there twice and other being absent from it.
    # I don't know the cause of the bug nor the solution... Lets see how it goes...
    res = []
    now = datetime.datetime.today()
    for muxxu_group in muxxu_groups:
        for page in range(1, 5):
            logging.debug("Extracting ranking from map {}, page {}".format(muxxu_group.map, page))
            content = get_source_code(
                "http://kingdom.muxxu.com/map/{}/ranks?sort=title;page={}".format(muxxu_group.map, page))
            res.append(RankingSource(muxxu_group.map, page, content))
            if '<div class="pages"> Page {0} / {0} </div>'.format(page) in content:
                break  # Last page found
    return res, now


def read_ranking_sources(ranking_sources, players, now):
    """ Update players with the ranking sources, giving a health of "None" for the new states.
    Take care to run this after the forum and the map as these data are used in it."""
    for ranking_source in ranking_sources:
        players_str = between('<table class="tablekingdom">', ranking_source.content, "</table>")
        for player_str in players_str.split('<tr>')[1:]:
            muxxu_id = int(between('<a href="/user/', player_str, '"'))
            if muxxu_id not in players:  # Should be seen on the forum or as newly born on the map before this is run.
                # => should only happen at the first runs of the program (until the whole "45" generation is dead)
                logging.info("{} n'est pas compté vu que né trop tôt".format(muxxu_id))
                continue
                # players[muxxu_id] = get_player_from_muxxu_id(muxxu_id)
            player = players[muxxu_id]
            age = re.search("(\d+) ans(?: et (\d+) mois)?", player_str)
            year, month = int(age.group(1)), int(age.group(2) or 0)
            assert max(player.states) < now, "Une donnée d'un temps futur a été trouvée, ce qui est inattendu..."
            player.states[now] = PlayerState(now, year, month, None)


def write_message(players, now):
    """ Generator generating each line of the "complete" message one after the other."""
    yield INTRO[:-5]
    for player in sorted(players.values(), key=lambda x: x.name):
        if now in player.states:  # seen in the rankings
            state = player.states[now]
            last_state_date = max([st.time for st in player.states.values() if st.health is not None])
            last_state = player.states[last_state_date]
            if last_state.health == 0:
                health = "né le {}".format(last_state.time.strftime("%d-%m-%Y %H:%M:%S"))
            else:
                health = SANTE[last_state.health]
            if 0 < last_state.health < SANTE.index("Excellente santé"):
                de6 = " + 1"
            elif SANTE.index("Excellente santé") <= last_state.health < len(SANTE)-2:
                days_in = (now.date() - player.last_born.date()).days
                de6 = " + ({{d100}} <= {})".format(get_threshold(days_in))
            else:
                de6 = ""
            yield "@{}:{}-{}-{}.{:02d} : {}{}".format(
                player.name, player.twino_id, player.muxxu_id, state.year, state.month, health, de6)
    yield "{}{}".format(ENDING[5:], now.strftime("%d-%m-%Y %H:%M:%S"))


def clean_message(players, last_date):
    """ Generator generating the "clean" message one line after the other and the messages to be sent to the players."""
    # TODO: generate the message to be sent to the players (including their maximum age of death)
    # TODO: add a note of the maximum age of death in the message
    yield "Etat de santé des différents joueurs le {}:".format(last_date.strftime("%d-%m-%Y"))
    for player in sorted(players.values(), key=lambda x: x.name):
        if last_date in player.states:
            health = player.states[last_date].health
            smiley = ("8)" if health < SANTE.index("Excellente santé") else
                      ":D" if health == SANTE.index("Excellente santé") else
                      ":)" if health == SANTE.index("Bonne santé") else
                      "°x°" if health == SANTE.index("Mauvaise santé") else
                      ":zombie:")
            yield "@{}:{}: {} {}".format(
                player.name, player.twino_id, SANTE[health], smiley)


def checks(now, last_date, players):
    """ Do all the checks to avoid cheatings"""
    # TODO: check le facteur de vieillissement (voir code commenté plus bas)
    # TODO: check les âges pour éviter des gens qui vivent plus vieux qu'autorisé
    # TODO: check que tous les états des joueurs se suivent bien, qu'aucun n'est sauté, etc.
    #  (si pas fait dans la fonction read_forum_sources)
    # TODO: check tout ce que j'aurais oublié :D.
    if now.date() > last_date.date() + datetime.timedelta(days=1):
        logging.warning("Attention : aucune donnée trouvée pour hier. Dernière date de données trouvées : {}"
                        "".format(last_date))
    elif now.date() < last_date.date():
        logging.error("Attention : évitez de voyager dans le temps, cela pose problème à mon algorithme :'(. "
                      "Les dernières données récoltées semblent provenir d'après l'instant présent.")


def main():
    add = 10  # used to do the simulations on the forum. Should be removed once validated
    muxxu_groups, threads, excepts = get_inputs()
    # logging.debug("{}, {}, {}".format(muxxu_groups, threads, excepts))
    threads = [64592595]  # used to do the simulations on the forum. Should be removed once validated
    forum_sources = get_from_forum(threads)
    # logging.debug(forum_sources)
    players, last_date = read_forum_sources(forum_sources, excepts)
    # logging.debug("{}, {}".format(players, last_date))
    now = datetime.datetime.today()
    now += datetime.timedelta(days=add)  # used to do the simulations on the forum. Should be removed once validated
    if last_date.date() == now.date():  # "complete" message already posted
        # TODO: do we want to do all the checks (but takes more time...)?
        message = list(clean_message(players, last_date))
    else:
        get_map_histo(muxxu_groups, players)
        # logging.debug(players)
        ranking_sources, now = get_rankings(muxxu_groups)
        now += datetime.timedelta(days=add)  # used to do the simulations on the forum. Should be removed once validated
        # logging.debug("{}, {}".format(ranking_sources, now))
        read_ranking_sources(ranking_sources, players, now)
        # logging.debug(players)
        message = list(write_message(players, now))

    time.sleep(1)  # to avoid mixing error messages and the message to be printed
    for line in message:
        print(line)
    time.sleep(1)  # to avoid mixing error messages and the message to be printed
    checks(now, last_date, players)


if __name__ == "__main__":
    main()

# """HereUnder is the code of Talsi to see the history of the map
# (finally unused because I disliked some parts of it and
# I like it a bit too much when things are coded the way I want it to be coded, sorry Talsi :$)."""
#
# # !/usr/bin/env python3
# import requests
# import re
#
# id_capital = 771130
# url = 'http://kingdom.muxxu.com/map?c=' + str(id_capital)
# map_page_source = requests.get(url).text
# map_page_source = map_page_source.split('\n')
# births_dict = dict()
# deaths_dict = dict()
#
# for row in map_page_source:
#     search_date = re.search('"datelog">Le (.*)</span>', row)
#     if (search_date):
#         date = search_date.group(1)
#
#     birth = re.search('Un jeune chevalier du nom de <a href="/user/(\d*)">(.*)</a> a pris', row)
#     if (birth):
#         user_id = birth.group(1)
#         user_name = birth.group(2)
#         if not user_id in births_dict:
#             births_dict[user_id] = [user_name, date]
#
#     death = re.search('Le <a href="/user/(\d*)">[a-zA-Z]* (.*)</a> est mort !', row)
#     if (death):
#         user_id = death.group(1)
#         user_name = death.group(2)
#         if not user_id in deaths_dict:
#             deaths_dict[user_id] = [user_name, date]
#
#     death = re.search('Le Royaume du <a href="/user/(\d*)">[a-zA-Z]* (.*)</a> s\'est effondré', row)
#     if (death):
#         user_id = death.group(1)
#         user_name = death.group(2)
#         if not user_id in deaths_dict:
#             deaths_dict[user_id] = [user_name, date]
#
# print("Naissances :")
# for key, value in births_dict.items():
#     print(f'{value[0]} ({key}) : {value[1]}')
#
# print("\nMorts :")
# for key, value in deaths_dict.items():
#     print(f'{value[0]} ({key}) : {value[1]}')

pass

# """Hereunder is the old code used to verify no one cheat"""
# import urllib.request
# import time
# import os
# from math import ceil
# import matplotlib.pyplot as plt
# import numpy as np
#
# maps = {3534: "classement_publi_01"}
# banned = {781272: 26 * 12 + 3}
# warnings = []
#
#
# def save_class(map_):
#     path_dir = maps[map_]
#     if not os.path.isdir(path_dir):
#         os.mkdir(path_dir)
#         print("new folder created")
#
#     hours = int(time.time() // 3600)
#     for page in range(1, 5):
#         time.sleep(1)
#         ranking_page = urllib.request.urlopen(
#             "http://kingdom.muxxu.com/map/{}/ranks?sort=title;page={}".format(map_, page)
#         ).read().decode("utf8")
#         with open(os.path.join(path_dir, "{:07d}.txt".format(hours)), "w" if page == 1 else "a") as f:
#             f.write(ranking_page)
#         if '<div class="pages"> Page {} / {} </div>'.format(page, page) in ranking_page:
#             break
#
#
# def check_rule(map_):
#     files = []
#     id2player = {}
#     all_players = set()
#     max_id = 0
#     for i, file in enumerate(sorted(os.listdir(maps[map_]))):
#         path = os.path.join(maps[map_], file)
#         hour = file.partition(".")[0]
#         with open(path, "r") as f:
#             players = []
#             player = None
#             for line in f.read().split("\n"):
#                 if '<a href="/user/' in line:
#                     player = int(line.partition('">')[0].partition('<td><a href="/user/')[2])
#                     all_players.add(player)
#                     id2player[player] = line.partition('</a></td>')[0][::-1].partition(" ")[0][::-1] + " ({})".format(
#                         player)
#                     max_id = max(max_id, player)
#                 if player is not None and "ans" in line:
#                     age = (int(line.partition("ans")[0]) * 12
#                            + (0 if "ans et" not in line else int(line.partition("ans et")[2].partition("mois")[0])))
#                     players.append((int(player), age))
#                     if age > 45 * 12:
#                         warnings.append("Too old player detected: {}".format(id2player[player]))
#                     elif age > 43 * 12:
#                         warnings.append("Old player detected: {}".format(id2player[player]))
#                     player = None
#             files.append((int(hour), sorted(players)))
#         print("File: {} ; players found: {}".format(files[-1][0], len(files[-1][1])))
#     files = sorted(files)
#
#     max_factor = 0
#     max_factor_player = {player: 0 for player in all_players}
#     for (hour1, players1) in files:
#         for (hour2, players2) in files:
#             if hour1 >= hour2:
#                 assert hour1 != hour2 or players1 == players2
#                 continue
#             players1 = players1 + [(max_id + 1, -1)]
#             iter1 = iter(players1)
#             (player1, age1) = next(iter1)
#             for (player2, age2) in players2:
#                 if player2 in banned:
#                     if age2 > banned[player2]:
#                         warnings.append("{} should be banned at {}".format(id2player[player2], banned[player2]))
#                     continue
#                 while (player2 > player1):
#                     (player1, age1) = next(iter1)
#                 turn_days = (hour2 - hour1) / 24.0 + 2
#                 old_age = age1 if player1 == player2 else 12 * 20
#                 age_taken = age2 - old_age
#                 factor = age_taken / 12.0 / turn_days
#                 max_factor_player[player2] = max(factor, max_factor_player[player2])
#                 if factor > max_factor:
#                     max_factor = max(factor, max_factor)
#                     max_player = player2
#                     max_hour1, max_hour2 = hour1, hour2
#                     max_age1, max_age2 = old_age, age2
#                 if age_taken > ceil(12 * 1.2 * turn_days):
#                     warnings.append(
#                         "Problem with {}, {}.{} to {}.{} in {} hours (between {} and {})".format(id2player[player2],
#                                                                                                  old_age // 12,
#                                                                                                  old_age % 12,
#                                                                                                  age2 // 12, age2 % 12,
#                                                                                                  hour2 - hour1, hour1,
#                                                                                                  hour2))
#     print("Maximum factor obtained by {} with {} between {} and {}, with {}.{} to {}.{} in {}d {}h".format(
#         id2player[max_player], max_factor, max_hour1, max_hour2, max_age1 // 12, max_age1 % 12, max_age2 // 12,
#                                                                  max_age2 % 12, (max_hour2 - max_hour1) // 24,
#                                                                  (max_hour2 - max_hour1) % 24))
#     [print("{} got maxi {}".format(id2player[player], max_fac)) for player, max_fac in
#      sorted(max_factor_player.items(), key=lambda x: x[1])]
#
#     days = [(hour - 440744) / 24.0 for hour, _ in files]
#     graphs = {player: np.ones(len(files)) * 20 for player in all_players}
#     for i, (_, players) in enumerate(files):
#         for player, age in players:
#             graphs[player][i] = age / 12.0
#     for player in sorted(graphs, key=lambda x: -graphs[x][-1]):
#         if np.sum(graphs[player][-20:]) > 20 * 20:
#             plt.plot(days, graphs[player], label=id2player[player])
#     plt.legend(loc="upper left", fontsize=6)
#     plt.xlim(days[-1] - 30, days[-1] + 1)
#
#
# for map_ in maps:
#     warnings.append("test")
#     save_class(map_)
#     check_rule(map_)
#     for warning in sorted(warnings):
#         print(warning)
#     plt.show()

pass

# """Hereunder is the code to find new players to invite to the publigroup
# (take care not to ask twice to the same players. Ask @simoons:528629 to be sure)."""
# import urllib.request
# import sys
# import time
#
# try:
#     with open("savelist.txt", 'r') as f:
#         to_contact = [line for line in f.read().split("\n") if len(line.strip()) > 0]
#     print(to_contact)
# except:
#     to_contact = []
#
# for map in range(3600):
#     print(map)
#     for page in range(1, 5):
#         print("\t" + str(page))
#         time.sleep(1)
#         ranking_page = urllib.request.urlopen(
#             "http://kingdom.muxxu.com/map/{}/ranks?sort=title;page={}".format(map, page)).read().decode("utf8")
#         users = [line.partition('<a href="/user/')[2].partition('">')[0] for line in ranking_page.split("\n") if
#                  '<a href="/user/' in line]
#         for user in users:
#             time.sleep(1)
#             profile_page = urllib.request.urlopen("http://kingdom.muxxu.com/user/{}".format(user)).read().decode("utf8")
#             if not len(profile_page.split('<div class="tid_user" tid_id="')) == 2:
#                 print("ERROR######################################\n", profile_page)
#             twinoid_id = profile_page.partition('<div class="tid_user" tid_id="')[2].partition('"')[0]
#             to_contact.append("@:{}".format(twinoid_id))
#
#         if len(users) < 30:
#             break
#     if map % 100 == 0:
#         with open("savelist_{}.txt".format(map), 'w') as f:
#             for contact in sorted(list(set(to_contact))):
#                 f.write(contact + "\n")
#
# with open("savelist.txt", 'w') as f:
#     for contact in sorted(list(set(to_contact))):
#         f.write(contact + "\n")