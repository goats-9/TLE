import random
import discord
import asyncio
import os
import math
import traceback
import time

from functools import cmp_to_key
from collections import namedtuple

from discord.ext import commands
from discord.ext.commands import cooldown, BucketType

#from utils import cf_api, discord_, codeforces, updation, elo, tournament_helper, challonge_api
#from constants import AUTO_UPDATE_TIME, ADMIN_PRIVILEGE_ROLES

from tle import constants
from tle.util import codeforces_common as cf_common
from tle.util import db
from tle.util import codeforces_api as cf
from tle.util import discord_common

MAX_ROUND_USERS = 5
LOWER_RATING = 800
UPPER_RATING = 3600
MATCH_DURATION = [5, 180]
MAX_PROBLEMS = 6
MAX_ALTS = 5
ROUNDS_PER_PAGE = 5

def _calc_round_score(users, status, times):
    def comp(a, b):
        if a[0] > b[0]:
            return -1
        if a[0] < b[0]:
            return 1
        if a[1] == b[1]:
            return 0
        return -1 if a[1] < b[1] else 1

    ranks = [[status[i], times[i], users[i]] for i in range(len(status))]
    ranks.sort(key=cmp_to_key(comp))
    res = []

    for user in ranks:
        User = namedtuple("User", "id points rank")
        # user points rank
        res.append(User(user[2], user[0], [[x[0], x[1]] for x in ranks].index([user[0], user[1]]) + 1))
    return res

class RoundCogError(commands.CommandError):
    pass

class Round(commands.Cog):
    def __init__(self, client):
        self.client = client
        # self.cf = cf_api.CodeforcesAPI()
        # self.api = challonge_api.ChallongeAPI(self.client)

    def _check_if_correct_channel(self, ctx):
        lockout_channel_id = cf_common.user_db.get_round_channel(ctx.guild.id)
        channel = ctx.guild.get_channel(lockout_channel_id)
        if not lockout_channel_id or ctx.channel.id != lockout_channel_id:
            raise RoundCogError('You must use this command in lockout round channel ({channel.mention}).')

    async def _check_if_all_members_ready(self, ctx, members):
        embed = discord.Embed(description=f"{' '.join(x.mention for x in members)} react on the message with ✅ within 30 seconds to join the round. {'Since you are the only participant, this will be a practice round and there will be no rating changes' if len(members) == 1 else ''}",
            color=discord.Color.purple())
        message = await ctx.send(embed=embed)
        await message.add_reaction("✅")

        # check for reaction of all users
        all_reacted = False
        reacted = []

        def check(reaction, member):
            return reaction.message.id == message.id and reaction.emoji == "✅" and member in members

        while True:
            try:
                _, member = await self.client.wait_for('reaction_add', timeout=30, check=check)
                reacted.append(member)
                if all(item in reacted for item in members):
                    all_reacted = True
                    break
            except asyncio.TimeoutError:
                break

        if not all_reacted:
            raise RoundCogError(f'Unable to start round, some participant(s) did not react in time!')

    def _check_if_any_member_is_already_in_round(self, ctx, members):
        busy_members = []
        for member in members:
            if cf_common.user_db.check_if_user_in_ongoing_round(ctx.guild.id, member.id):
                busy_members.append(member)
        if busy_members:
            busy_members_str = ", ".join([ctx.guild.get_member(int(member.id)).mention for member in busy_members])
            error = f'{busy_members_str} are registered in ongoing lockout rounds.'
            raise RoundCogError(error)

    async def _get_time_response(self, client, ctx, message, time, author, range_):
        original = await ctx.send(embed=discord.Embed(description=message, color=discord.Color.green()))

        def check(m):
            if not m.content.isdigit() or not m.author == author:
                return False
            i = m.content
            if int(i) < range_[0] or int(i) > range_[1]:
                return False
            return True
        try:
            msg = await client.wait_for('message', timeout=time, check=check)
            await original.delete()
            return int(msg.content)
        except asyncio.TimeoutError:
            await original.delete()
            raise RoundCogError(f'{ctx.author.mention} you took too long to decide')

    async def _get_seq_response(self, client, ctx, message, time, length, author, range_):
        original = await ctx.send(embed=discord.Embed(description=message, color=discord.Color.green()))

        def check(m):
            if m.author != author:
                return False
            data = m.content.split()
            if len(data) != length:
                return False
            for i in data:
                if not i.isdigit():
                    return False
                if int(i) < range_[0] or int(i) > range_[1]:
                    return False
            return True

        try:
            msg = await client.wait_for('message', timeout=time, check=check)
            await original.delete()
            return [int(x) for x in msg.content.split()]
        except asyncio.TimeoutError:
            await original.delete()
            raise RoundCogError(f'{ctx.author.mention} you took too long to decide')

    def _round_problems_embed(self, round_info):
        ranklist = _calc_round_score(list(map(int, round_info.users.split())), list(map(int, round_info.status.split())), list(map(int, round_info.times.split())))

        problemEntries = round_info.problems.split()
        def get_problem(problemContestId, problemIndex):
            return [prob for prob in cf_common.cache2.problem_cache.problems
                    if prob.contestId == problemContestId and prob.index == problemIndex]

        problems = [get_problem(prob.split('/')[0], prob.split('/')[1]) if prob != '0' else None for prob in problemEntries]

        replacementStr = 'This problem has been solved' if round_info.repeat == 0 else 'No problems of this rating left'
        names = [f'[{prob.name}](https://codeforces.com/contest/{prob.contestId}/problem/{prob.index})' 
                    if problemEntries[i] != '0' else replacementStr for prob in problems]

        desc = ""
        for user in ranklist:
            emojis = [':first_place:', ':second_place:', ':third_place:']
            handle = cf_common.user_db.get_handle(user.id, round_info.guild) 
            desc += f'{emojis[user.rank-1] if user.rank <= len(emojis) else user.rank} [{handle}](https://codeforces.com/profile/{handle}) **{user.points}** points\n'

        embed = discord.Embed(description=desc, color=discord.Color.magenta())
        embed.set_author(name=f'Problems')

        embed.add_field(name='Points', value='\n'.join(round_info.points.split()), inline=True)
        embed.add_field(name='Problem Name', value='\n'.join(names), inline=True)
        embed.add_field(name='Rating', value='\n'.join(round_info.rating.split()), inline=True)
        timestr = cf_common.pretty_time_format(((round_info.time + 60 * round_info.duration) - int(time.time())), shorten=True, always_seconds=True)
        embed.set_footer(text=f'Time left: {timestr}')

        return embed
    
    def make_round_embed(self, ctx):
        desc = "Information about Round related commands! **[use ;round <command>]**\n\n"
        match = self.client.get_command('round')

        for cmd in match.commands:
            desc += f"`{cmd.name}`: **{cmd.brief}**\n"
        embed = discord.Embed(description=desc, color=discord.Color.dark_magenta())
        embed.set_author(name="Lockout commands help", icon_url=ctx.me.avatar)
        embed.set_footer(
            text="Use the prefix ; before each command. For detailed usage about a particular command, type ;help round <command>")
        embed.add_field(name="GitHub repository", value=f"[GitHub](https://github.com/pseudocoder10/Lockout-Bot)",
                        inline=True)
        embed.add_field(name="Bot Invite link",
                        value=f"[Invite](https://discord.com/oauth2/authorize?client_id=669978762120790045&permissions=0&scope=bot)",
                        inline=True)
        embed.add_field(name="Support Server", value=f"[Server](https://discord.gg/xP2UPUn)",
                        inline=True)
        return embed

    @commands.group(brief='Commands related to lockout rounds! Type ;round for more details', invoke_without_command=True)
    async def round(self, ctx):
        await ctx.send(embed=self.make_round_embed(ctx))

    @round.command(brief='Set the lockout channel to the current channel')
    @commands.has_any_role(constants.TLE_ADMIN, constants.TLE_MODERATOR)  # OK
    async def set_channel(self, ctx):
        """ Sets the lockout round channel to the current channel.
        """
        cf_common.user_db.set_round_channel(ctx.guild.id, ctx.channel.id)
        await ctx.send(embed=discord_common.embed_success('Lockout round channel saved successfully'))

    @round.command(brief='Get the lockout channel')
    async def get_channel(self, ctx):
        """ Gets the lockout round channel.
        """
        channel_id = cf_common.user_db.get_round_channel(ctx.guild.id)
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            raise RoundCogError('There is no lockout round channel')
        embed = discord_common.embed_success('Current lockout round channel')
        embed.add_field(name='Channel', value=channel.mention)
        await ctx.send(embed=embed)


    @round.command(name="challenge", brief="Challenge multiple users to a round")
    async def challenge(self, ctx, *members: discord.Member):
        # check if we are in the correct channel
        self._check_if_correct_channel(ctx)
        
        members = list(set(members))
        if len(members) == 0:
            raise RoundCogError('The correct usage is `;round challenge @user1 @user2...`')            
        if ctx.author not in members:
            members.append(ctx.author)
        if len(members) > MAX_ROUND_USERS:
            raise RoundCogError(f'{ctx.author.mention} atmost {MAX_ROUND_USERS} users can compete at a time') 

        await self._check_if_all_members_ready(ctx, members)           

        problem_cnt = await self._get_time_response(self.client, ctx, f"{ctx.author.mention} enter the number of problems between [1, {MAX_PROBLEMS}]", 30, ctx.author, [1, MAX_PROBLEMS])

        duration = await self._get_time_response(self.client, ctx, f"{ctx.author.mention} enter the duration of match in minutes between {MATCH_DURATION}", 30, ctx.author, MATCH_DURATION)

        ratings = await self._get_seq_response(self.client, ctx, f"{ctx.author.mention} enter {problem_cnt} space seperated integers denoting the ratings of problems (between {LOWER_RATING} and {UPPER_RATING})", 60, problem_cnt, ctx.author, [LOWER_RATING, UPPER_RATING])

        points = await self._get_seq_response(self.client, ctx, f"{ctx.author.mention} enter {problem_cnt} space seperated integer denoting the points of problems (between 100 and 10,000)", 60, problem_cnt, ctx.author, [100, 10000])

        repeat = await self._get_time_response(self.client, ctx, f"{ctx.author.mention} do you want a new problem to appear when someone solves a problem (type 1 for yes and 0 for no)", 30, ctx.author, [0, 1])

        # check for members still in a round
        self._check_if_any_member_is_already_in_round(ctx, members)

        # pick problem
        handles = cf_common.members_to_handles(members, ctx.guild.id)
        submissions = [await cf.user.status(handle=handle) for handle in handles]
        solved = {sub.problem.name for subs in submissions for sub in subs if sub.verdict != 'COMPILATION_ERROR'} 

        def get_problems(rating):
            return [prob for prob in cf_common.cache2.problem_cache.problems
                    if prob.rating == rating and prob.name not in solved
                    and not any(cf_common.is_contest_writer(prob.contestId, handle) for handle in handles)
                    and not cf_common.is_nonstandard_problem(prob)]

        selected = []
        for rating in ratings:
            problems = get_problems(rating)
            problems = [p for p in problems if p not in selected]
            problems.sort(key=lambda problem: cf_common.cache2.contest_cache.get_contest(problem.contestId).startTimeSeconds)

            if not problems:
                raise RoundCogError(f'Not enough unsolved problems of rating {rating} available.')
            choice = max(random.randrange(len(problems)) for _ in range(5)) 
            problem = problems[choice]            
            selected.append(problem)

        await ctx.send(embed=discord.Embed(description="Starting the round...", color=discord.Color.green()))

        cf_common.user_db.create_round(ctx.guild.id, time.time(), members, ratings, points, selected, duration, repeat)
        round_info = cf_common.user_db.get_round_info(ctx.guild.id, members[0].id)

        await ctx.send(embed=self._round_problems_embed(round_info))

    @round.command(brief="Invalidate a round (Admin/Mod/Lockout Manager only)")
    @commands.has_any_role(constants.TLE_ADMIN, constants.TLE_MODERATOR)  # OK
    async def _invalidate(self, ctx, member: discord.Member):
        if not cf_common.user_db.check_if_user_in_ongoing_round(ctx.guild.id, member.id):
            raise RoundCogError(f'{member.mention} is not in a round')
        cf_common.user_db.delete_round(ctx.guild.id, member.id)
        await ctx.send(f'Round deleted.')

    @round.command(brief="View problems of a round")
    async def problems(self, ctx, member: discord.Member=None):
        if not member:
            member = ctx.author
        if not cf_common.user_db.check_if_user_in_ongoing_round(ctx.guild.id, member.id):
            raise RoundCogError(f'{member.mention} is not in a round')

        round_info = cf_common.user_db.get_round_info(ctx.guild.id, member.id)
        await ctx.send(embed=self._round_problems_embed(round_info))

#     @round.command(brief="Update matches status for the server")
#     @cooldown(1, AUTO_UPDATE_TIME, BucketType.guild)
#     async def update(self, ctx):
#         await ctx.send(embed=discord.Embed(description="Updating rounds for this server", color=discord.Color.green()))
#         rounds = self.db.get_all_rounds(ctx.guild.id)

#         for round in rounds:
#             try:
#                 resp = await updation.update_round(round)
#                 if not resp[0]:
#                     logging_channel = await self.client.fetch_channel(os.environ.get("LOGGING_CHANNEL"))
#                     await logging_channel.send(f"Error while updating rounds: {resp[1]}")
#                     continue
#                 resp = resp[1]
#                 channel = self.client.get_channel(round.channel)

#                 if resp[2] or resp[1]:
#                     await channel.send(f"{' '.join([(await discord_.fetch_member(ctx.guild, int(m))).mention for m in round.users.split()])} there is an update in standings")

#                 for i in range(len(resp[0])):
#                     if len(resp[0][i]):
#                         await channel.send(embed=discord.Embed(
#                             description=f"{' '.join([(await discord_.fetch_member(ctx.guild, m)).mention for m in resp[0][i]])} has solved problem worth **{round.points.split()[i]}** points",
#                             color=discord.Color.blue()))

#                 if not resp[1] and resp[2]:
#                     new_info = self.db.get_round_info(round.guild, round.users)
#                     await channel.send(embed=discord_.round_problems_embed(new_info))

#                 if resp[1]:
#                     round_info = self.db.get_round_info(round.guild, round.users)
#                     ranklist = updation.round_score(list(map(int, round_info.users.split())),
#                                            list(map(int, round_info.status.split())),
#                                            list(map(int, round_info.times.split())))
#                     eloChanges = elo.calculateChanges([[(await discord_.fetch_member(ctx.guild, user.id)), user.rank, self.db.get_match_rating(round_info.guild, user.id)[-1]] for user in ranklist])

#                     for id in list(map(int, round_info.users.split())):
#                         self.db.add_rating_update(round_info.guild, id, eloChanges[id][0])

#                     self.db.delete_round(round_info.guild, round_info.users)
#                     self.db.add_to_finished_rounds(round_info)

#                     embed = discord.Embed(color=discord.Color.dark_magenta())
#                     pos, name, ratingChange = '', '', ''
#                     for user in ranklist:
#                         handle = self.db.get_handle(round_info.guild, user.id)
#                         emojis = [":first_place:", ":second_place:", ":third_place:"]
#                         pos += f"{emojis[user.rank-1] if user.rank <= len(emojis) else str(user.rank)} **{user.points}**\n"
#                         name += f"[{handle}](https://codeforces.com/profile/{handle})\n"
#                         ratingChange += f"{eloChanges[user.id][0]} (**{'+' if eloChanges[user.id][1] >= 0 else ''}{eloChanges[user.id][1]}**)\n"
#                     embed.add_field(name="Position", value=pos)
#                     embed.add_field(name="User", value=name)
#                     embed.add_field(name="Rating changes", value=ratingChange)
#                     embed.set_author(name=f"Round over! Final standings")
#                     await channel.send(embed=embed)

#                     if round_info.tournament == 1:
#                         tournament_info = self.db.get_tournament_info(round_info.guild)
#                         if not tournament_info or tournament_info.status != 2:
#                             continue
#                         if ranklist[1].rank == 1 and tournament_info.type != 2:
#                             await discord_.send_message(channel, "Since the round ended in a draw, you will have to compete again for it to be counted in the tournament")
#                         else:
#                             res = await tournament_helper.validate_match(round_info.guild, ranklist[0].id, ranklist[1].id, self.api, self.db)
#                             if not res[0]:
#                                 await discord_.send_message(channel, res[1] + "\n\nIf you think this is a mistake, type `.tournament forcewin <handle>` to grant victory to a user")
#                             else:
#                                 draw = True if ranklist[1].rank == 1 else False
#                                 scores = f"{ranklist[0].points}-{ranklist[1].points}" if res[1]['player1'] == res[1][
#                                     ranklist[0].id] else f"{ranklist[1].points}-{ranklist[0].points}"
#                                 match_resp = await self.api.post_match_results(res[1]['tournament_id'], res[1]['match_id'], scores, res[1][ranklist[0].id] if not draw else "tie")
#                                 if not match_resp or 'errors' in match_resp:
#                                     await discord_.send_message(channel, "Some error occurred while validating tournament match. \n\nType `.tournament forcewin <handle>` to grant victory to a user manually")
#                                     if match_resp and 'errors' in match_resp:
#                                         logging_channel = await self.client.fetch_channel(os.environ.get("LOGGING_CHANNEL"))
#                                         await logging_channel.send(f"Error while validating tournament rounds: {match_resp['errors']}")
#                                     continue
#                                 winner_handle = self.db.get_handle(round_info.guild, ranklist[0].id)
#                                 await discord_.send_message(channel, f"{f'Congrats **{winner_handle}** for qualifying to the next round. :tada:' if not draw else 'The round ended in a draw!'}\n\nTo view the list of future tournament rounds, type `.tournament matches`")
#                                 if await tournament_helper.validate_tournament_completion(round_info.guild, self.api, self.db):
#                                     await self.api.finish_tournament(res[1]['tournament_id'])
#                                     await asyncio.sleep(3)
#                                     winner_handle = await tournament_helper.get_winner(res[1]['tournament_id'], self.api)
#                                     await channel.send(embed=tournament_helper.tournament_over_embed(round_info.guild, winner_handle, self.db))
#                                     self.db.add_to_finished_tournaments(self.db.get_tournament_info(round_info.guild), winner_handle)
#                                     self.db.delete_tournament(round_info.guild)

#             except Exception as e:
#                 logging_channel = await self.client.fetch_channel(os.environ.get("LOGGING_CHANNEL"))
#                 await logging_channel.send(f"Error while updating rounds: {str(traceback.format_exc())}")



#     @round.command(name="ongoing", brief="View ongoing rounds")
#     async def ongoing(self, ctx):
#         data = self.db.get_all_rounds(ctx.guild.id)

#         content = discord_.ongoing_rounds_embed(data)

#         if len(content) == 0:
#             await discord_.send_message(ctx, f"No ongoing rounds")
#             return

#         currPage = 0
#         totPage = math.ceil(len(content) / ROUNDS_PER_PAGE)
#         text = '\n'.join(content[currPage * ROUNDS_PER_PAGE: min(len(content), (currPage + 1) * ROUNDS_PER_PAGE)])
#         embed = discord.Embed(description=text, color=discord.Color.blurple())
#         embed.set_author(name="Ongoing Rounds")
#         embed.set_footer(text=f"Page {currPage + 1} of {totPage}")
#         message = await ctx.send(embed=embed)

#         await message.add_reaction("⏮")
#         await message.add_reaction("◀")
#         await message.add_reaction("▶")
#         await message.add_reaction("⏭")

#         def check(reaction, user):
#             return reaction.message.id == message.id and reaction.emoji in ["⏮", "◀", "▶",
#                                                                             "⏭"] and user != self.client.user

#         while True:
#             try:
#                 reaction, user = await self.client.wait_for('reaction_add', timeout=90, check=check)
#                 try:
#                     await reaction.remove(user)
#                 except Exception:
#                     pass
#                 if reaction.emoji == "⏮":
#                     currPage = 0
#                 elif reaction.emoji == "◀":
#                     currPage = max(currPage - 1, 0)
#                 elif reaction.emoji == "▶":
#                     currPage = min(currPage + 1, totPage - 1)
#                 else:
#                     currPage = totPage - 1
#                 text = '\n'.join(
#                     content[currPage * ROUNDS_PER_PAGE: min(len(content), (currPage + 1) * ROUNDS_PER_PAGE)])
#                 embed = discord.Embed(description=text, color=discord.Color.blurple())
#                 embed.set_author(name="Ongoing rounds")
#                 embed.set_footer(text=f"Page {currPage + 1} of {totPage}")
#                 await message.edit(embed=embed)

#             except asyncio.TimeoutError:
#                 break



#     @round.command(name="recent", brief="Show recent rounds")
#     async def recent(self, ctx, user: discord.Member=None):
#         data = self.db.get_recent_rounds(ctx.guild.id, str(user.id) if user else None)

#         content = discord_.recent_rounds_embed(data)

#         if len(content) == 0:
#             await discord_.send_message(ctx, f"No recent rounds")
#             return

#         currPage = 0
#         totPage = math.ceil(len(content) / ROUNDS_PER_PAGE)
#         text = '\n'.join(content[currPage * ROUNDS_PER_PAGE: min(len(content), (currPage + 1) * ROUNDS_PER_PAGE)])
#         embed = discord.Embed(description=text, color=discord.Color.blurple())
#         embed.set_author(name="Recent Rounds")
#         embed.set_footer(text=f"Page {currPage + 1} of {totPage}")
#         message = await ctx.send(embed=embed)

#         await message.add_reaction("⏮")
#         await message.add_reaction("◀")
#         await message.add_reaction("▶")
#         await message.add_reaction("⏭")

#         def check(reaction, user):
#             return reaction.message.id == message.id and reaction.emoji in ["⏮", "◀", "▶",
#                                                                             "⏭"] and user != self.client.user

#         while True:
#             try:
#                 reaction, user = await self.client.wait_for('reaction_add', timeout=90, check=check)
#                 try:
#                     await reaction.remove(user)
#                 except Exception:
#                     pass
#                 if reaction.emoji == "⏮":
#                     currPage = 0
#                 elif reaction.emoji == "◀":
#                     currPage = max(currPage - 1, 0)
#                 elif reaction.emoji == "▶":
#                     currPage = min(currPage + 1, totPage - 1)
#                 else:
#                     currPage = totPage - 1
#                 text = '\n'.join(
#                     content[currPage * ROUNDS_PER_PAGE: min(len(content), (currPage + 1) * ROUNDS_PER_PAGE)])
#                 embed = discord.Embed(description=text, color=discord.Color.blurple())
#                 embed.set_author(name="Recent rounds")
#                 embed.set_footer(text=f"Page {currPage + 1} of {totPage}")
#                 await message.edit(embed=embed)

#             except asyncio.TimeoutError:
#                 break

# #     @round.command(name="invalidate", brief="Invalidate your round")
# #     async def invalidate(self, ctx):
# #         if not self.db.in_a_round(ctx.guild.id, ctx.author.id):
# #             await ctx.send(f"{ctx.author.mention} you are not in a round")
# #             return
# #
# #         data = self.db.get_round_info(ctx.guild.id, ctx.author.id)
# #         try:
# #             users = [await ctx.guild.fetch_member(int(x)) for x in data[1].split()]
# #         except Exception:
# #             await ctx.send(f"{ctx.author.mention} some error occurred! Maybe one of the participants left the server")
# #             return
# #
# #         msg = await ctx.send(f"{' '.join([x.mention for x in users])} react within 30 seconds to invalidate the match")
# #         await msg.add_reaction("✅")
# #
# #         await asyncio.sleep(30)
# #         message = await ctx.channel.fetch_message(msg.id)
# #
# #         reaction = None
# #         for x in message.reactions:
# #             if x.emoji == "✅":
# #                 reaction = x
# #
# #         reacted = await reaction.users().flatten()
# #         for i in users:
# #             if i not in reacted:
# #                 await ctx.send(f"Unable to invalidate round, {i.name} did not react in time!")
# #                 return
# #
# #         self.db.delete_round(ctx.guild.id, ctx.author.id)
# #         await ctx.send(f"Match has been invalidated")
# #



#     @round.command(name="custom", brief="Challenge to a round with custom problemset")
#     async def custom(self, ctx, *users: discord.Member):
#         users = list(set(users))
#         if len(users) == 0:
#             await discord_.send_message(ctx, f"The correct usage is `.round custom @user1 @user2...`")
#             return
#         if ctx.author not in users:
#             users.append(ctx.author)
#         if len(users) > MAX_ROUND_USERS:
#             await ctx.send(f"{ctx.author.mention} atmost {MAX_ROUND_USERS} users can compete at a time")
#             return
#         for i in users:
#             if not self.db.get_handle(ctx.guild.id, i.id):
#                 await discord_.send_message(ctx, f"Handle for {i.mention} not set! Use `.handle identify` to register")
#                 return
#             if self.db.in_a_round(ctx.guild.id, i.id):
#                 await discord_.send_message(ctx, f"{i.mention} is already in a round!")
#                 return

#         embed = discord.Embed(
#             description=f"{' '.join(x.mention for x in users)} react on the message with ✅ within 30 seconds to join the round. {'Since you are the only participant, this will be a practice round and there will be no rating changes' if len(users) == 1 else ''}",
#             color=discord.Color.purple())
#         message = await ctx.send(embed=embed)
#         await message.add_reaction("✅")

#         all_reacted = False
#         reacted = []

#         def check(reaction, user):
#             return reaction.message.id == message.id and reaction.emoji == "✅" and user in users

#         while True:
#             try:
#                 reaction, user = await self.client.wait_for('reaction_add', timeout=30, check=check)
#                 reacted.append(user)
#                 if all(item in reacted for item in users):
#                     all_reacted = True
#                     break
#             except asyncio.TimeoutError:
#                 break

#         if not all_reacted:
#             await discord_.send_message(ctx, f"Unable to start round, some participant(s) did not react in time!")
#             return

#         problem_cnt = await discord_.get_time_response(self.client, ctx,
#                                                        f"{ctx.author.mention} enter the number of problems between [1, {MAX_PROBLEMS}]",
#                                                        30, ctx.author, [1, MAX_PROBLEMS])
#         if not problem_cnt[0]:
#             await discord_.send_message(ctx, f"{ctx.author.mention} you took too long to decide")
#             return
#         problem_cnt = problem_cnt[1]

#         duration = await discord_.get_time_response(self.client, ctx,
#                                                     f"{ctx.author.mention} enter the duration of match in minutes between {MATCH_DURATION}",
#                                                     30, ctx.author, MATCH_DURATION)
#         if not duration[0]:
#             await discord_.send_message(ctx, f"{ctx.author.mention} you took too long to decide")
#             return
#         duration = duration[1]

#         problems = await discord_.get_problems_response(self.client, ctx,
#                                                  f"{ctx.author.mention} enter {problem_cnt} space seperated problem ids denoting the problems. Eg: `123/A 455/B 242/C ...`",
#                                                  60, problem_cnt, ctx.author)
#         if not problems[0]:
#             await discord_.send_message(ctx, f"{ctx.author.mention} you took too long to decide")
#             return
#         problems = problems[1]

#         points = await discord_.get_seq_response(self.client, ctx,
#                                                  f"{ctx.author.mention} enter {problem_cnt} space seperated integer denoting the points of problems (between 100 and 10,000)",
#                                                  60, problem_cnt, ctx.author, [100, 10000])
#         if not points[0]:
#             await discord_.send_message(ctx, f"{ctx.author.mention} you took too long to decide")
#             return
#         points = points[1]

#         for i in users:
#             if self.db.in_a_round(ctx.guild.id, i.id):
#                 await discord_.send_message(ctx, f"{i.name} is already in a round!")
#                 return
#         rating = [problem.rating for problem in problems]

#         tournament = 0
#         if len(users) == 2 and (await tournament_helper.is_a_match(ctx.guild.id, users[0].id, users[1].id, self.api, self.db)):
#             tournament = await discord_.get_time_response(self.client, ctx,
#                                                           f"{ctx.author.mention} this round is a part of the tournament. Do you want the result of this round to be counted in the tournament. Type `1` for yes and `0` for no",
#                                                           30, ctx.author, [0, 1])
#             if not tournament[0]:
#                 await discord_.send_message(ctx, f"{ctx.author.mention} you took too long to decide")
#                 return
#             tournament = tournament[1]

#         await ctx.send(embed=discord.Embed(description="Starting the round...", color=discord.Color.green()))
#         self.db.add_to_ongoing_round(ctx, users, rating, points, problems, duration, 0, [], tournament)
#         round_info = self.db.get_round_info(ctx.guild.id, users[0].id)

#         await ctx.send(embed=discord_.round_problems_embed(round_info))

    @discord_common.send_error_if(RoundCogError, cf_common.ResolveHandleError,
                                  cf_common.FilterError)
    async def cog_command_error(self, ctx, error):
        pass

async def setup(bot):
    await bot.add_cog(Round(bot))