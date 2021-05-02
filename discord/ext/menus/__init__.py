# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2015-2019 Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

from __future__ import annotations

import asyncio
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    cast,
    Dict,
    Generic,
    NoReturn,
    List,
    Optional,
    Pattern,
    Protocol,
    Sequence,
    Tuple,
    TYPE_CHECKING,
    Type,
    TypeVar,
    Union,
)
import discord

import itertools
import inspect
import logging
import re

if TYPE_CHECKING:
    from discord.ext import commands

    class SupportsLessThan(Protocol):
        def __lt__(self, other: Any) -> bool:
            ...


T = TypeVar('T')
KT = TypeVar('KT', bound='SupportsLessThan')
IT = TypeVar('IT')
MNUT = TypeVar('MNUT', bound='Menu')

_BtnFunc = Callable[[MNUT, discord.RawReactionActionEvent], T]
_MbyCoro = Union[None, Awaitable[Union[Any, NoReturn]]]

# Needed for the setup.py script
__version__ = '1.0.0-a'

# consistency with the `discord` namespaced logging
log = logging.getLogger(__name__)


class MenuError(Exception):
    pass


class CannotEmbedLinks(MenuError):
    def __init__(self) -> None:
        super().__init__('Bot does not have embed links permission in this channel.')


class CannotSendMessages(MenuError):
    def __init__(self) -> None:
        super().__init__('Bot cannot send messages in this channel.')


class CannotAddReactions(MenuError):
    def __init__(self) -> None:
        super().__init__('Bot cannot add reactions in this channel.')


class CannotReadMessageHistory(MenuError):
    def __init__(self) -> None:
        super().__init__('Bot does not have Read Message History permissions in this channel.')


class Position:
    __slots__ = ('number', 'bucket')

    def __init__(self, number: int, *, bucket: int = 1) -> None:
        self.bucket = bucket
        self.number = number

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Position) or not isinstance(self, Position):
            return NotImplemented

        return (self.bucket, self.number) < (other.bucket, other.number)

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Position) and other.bucket == self.bucket and other.number == self.number

    def __le__(self, other: Any) -> bool:
        r = Position.__lt__(other, self)
        if r is NotImplemented:
            return NotImplemented
        return not r

    def __gt__(self, other: Any) -> bool:
        return Position.__lt__(other, self)

    def __ge__(self, other: Any) -> bool:
        r = Position.__lt__(self, other)
        if r is NotImplemented:
            return NotImplemented
        return not r

    def __repr__(self) -> str:
        return '<{0.__class__.__name__}: {0.number}>'.format(self)


class Last(Position):
    __slots__ = ()

    def __init__(self, number: int = 0) -> None:
        super().__init__(number, bucket=2)


class First(Position):
    __slots__ = ()

    def __init__(self, number: int = 0) -> None:
        super().__init__(number, bucket=0)


_custom_emoji: Pattern[str] = re.compile(r'<?(?P<animated>a)?:?(?P<name>[A-Za-z0-9\_]+):(?P<id>[0-9]{13,20})>?')


def _cast_emoji(obj: Any, *, _custom_emoji: Pattern[str] = _custom_emoji) -> discord.PartialEmoji:
    if isinstance(obj, discord.PartialEmoji):
        return obj

    obj = str(obj)
    match = _custom_emoji.match(obj)
    if match is not None:
        groups = match.groupdict()
        animated = bool(groups['animated'])
        emoji_id = int(groups['id'])
        name = groups['name']
        return discord.PartialEmoji(name=name, animated=animated, id=emoji_id)
    return discord.PartialEmoji(name=obj, id=None, animated=False)


class Button:
    """Represents a reaction-style button for the :class:`Menu`.

    There are two ways to create this, the first being through explicitly
    creating this class and the second being through the decorator interface,
    :func:`button`.

    The action must have both a ``self`` and a ``payload`` parameter
    of type :class:`discord.RawReactionActionEvent`.

    Attributes
    ------------
    emoji: :class:`discord.PartialEmoji`
        The emoji to use as the button. Note that passing a string will
        transform it into a :class:`discord.PartialEmoji`.
    action
        A coroutine that is called when the button is pressed.
    skip_if: Optional[Callable[[:class:`Menu`], :class:`bool`]]
        A callable that detects whether it should be skipped.
        A skipped button does not show up in the reaction list
        and will not be processed.
    position: :class:`Position`
        The position the button should have in the initial order.
        Note that since Discord does not actually maintain reaction
        order, this is a best effort attempt to have an order until
        the user restarts their client. Defaults to ``Position(0)``.
    lock: :class:`bool`
        Whether the button should lock all other buttons from being processed
        until this button is done. Defaults to ``True``.
    """

    __slots__ = ('emoji', '_action', '_skip_if', 'position', 'lock')

    def __init__(
        self,
        emoji: discord.PartialEmoji,
        action: _BtnFunc,
        *,
        skip_if: Optional[Callable[[Menu], bool]] = None,
        position: Optional[Position] = None,
        lock: bool = True
    ):
        self.emoji = _cast_emoji(emoji)
        self.action = action
        self.skip_if = skip_if
        self.position = position or Position(0)
        self.lock = lock

    @property
    def skip_if(self) -> Callable[[Menu], bool]:
        return self._skip_if

    @skip_if.setter
    def skip_if(self, value: Optional[Callable[[Menu], bool]]) -> None:  # type: ignore
        if value is None:
            self._skip_if = lambda x: False
            return

        try:
            menu_self = value.__self__
        except AttributeError:
            self._skip_if = value
        else:
            # Unfurl the method to not be bound
            if not isinstance(menu_self, Menu):
                raise TypeError('skip_if bound method must be from Menu not %r' % menu_self)

            self._skip_if = value.__func__

    @property
    def action(self) -> _BtnFunc:
        return self._action

    @action.setter
    def action(self, value: _BtnFunc) -> None:
        try:
            menu_self = value.__self__
        except AttributeError:
            pass
        else:
            # Unfurl the method to not be bound
            if not isinstance(menu_self, Menu):
                raise TypeError('action bound method must be from Menu not %r' % menu_self)

            value = value.__func__

        if not inspect.iscoroutinefunction(value):
            raise TypeError('action must be a coroutine not %r' % value)

        self._action = value

    def __call__(self, menu: Menu, payload: discord.RawReactionActionEvent) -> Optional[Any]:
        if self.skip_if(menu):
            return
        return self._action(menu, payload)

    def __str__(self) -> str:
        return str(self.emoji)

    def is_valid(self, menu) -> bool:
        return not self.skip_if(menu)


def button(emoji: Union[str, discord.PartialEmoji], **kwargs: Any) -> Callable[[_BtnFunc], _BtnFunc]:
    """Denotes a method to be button for the :class:`Menu`.

    The methods being wrapped must have both a ``self`` and a ``payload``
    parameter of type :class:`discord.RawReactionActionEvent`.

    The keyword arguments are forwarded to the :class:`Button` constructor.

    Example
    ---------

    .. code-block:: python3

        class MyMenu(Menu):
            async def send_initial_message(self, ctx, channel):
                return await channel.send(f'Hello {ctx.author}')

            @button('\\N{THUMBS UP SIGN}')
            async def on_thumbs_up(self, payload):
                await self.message.edit(content=f'Thanks {self.ctx.author}!')

            @button('\\N{THUMBS DOWN SIGN}')
            async def on_thumbs_down(self, payload):
                await self.message.edit(content=f"That's not nice {self.ctx.author}...")

    Parameters
    ------------
    emoji: Union[:class:`str`, :class:`discord.PartialEmoji`]
        The emoji to use for the button.
    """

    def decorator(func):
        func.__menu_button__ = _cast_emoji(emoji)
        func.__menu_button_kwargs__ = kwargs
        return func

    return decorator


class _MenuMeta(type):
    if TYPE_CHECKING:
        __menu_buttons__: List[_BtnFunc]

    def __new__(cls: Type[type], name: str, bases: Tuple[type, ...], attrs: Dict[str, Any], **kwargs: Any):
        buttons = []

        new_cls = cast(Type[_MenuMeta], super().__new__(cls, name, bases, attrs))

        inherit_buttons = kwargs.pop('inherit_buttons', True)
        if inherit_buttons:
            # walk MRO to get all buttons even in subclasses
            for base in reversed(new_cls.__mro__):
                for elem, value in base.__dict__.items():
                    try:
                        value.__menu_button__
                    except AttributeError:
                        continue
                    else:
                        buttons.append(value)
        else:
            for elem, value in attrs.items():
                try:
                    value.__menu_button__
                except AttributeError:
                    continue
                else:
                    buttons.append(value)

        new_cls.__menu_buttons__ = buttons

        return type.__new__(cls, name, bases, attrs)

    def get_buttons(cls) -> Dict[discord.PartialEmoji, Button]:
        buttons = {}
        for func in cls.__menu_buttons__:
            emoji = func.__menu_button__
            buttons[emoji] = Button(emoji, func, **func.__menu_button_kwargs__)
        return buttons


class Menu(metaclass=_MenuMeta):
    r"""An interface that allows handling menus by using reactions as buttons.

    Buttons should be marked with the :func:`button` decorator. Please note that
    this expects the methods to have a single parameter, the ``payload``. This
    ``payload`` is of type :class:`discord.RawReactionActionEvent`.

    Attributes
    ------------
    timeout: :class:`float`
        The timeout to wait between button inputs.
    delete_message_after: :class:`bool`
        Whether to delete the message after the menu interaction is done.
    clear_reactions_after: :class:`bool`
        Whether to clear reactions after the menu interaction is done.
        Note that :attr:`delete_message_after` takes priority over this attribute.
        If the bot does not have permissions to clear the reactions then it will
        delete the reactions one by one.
    check_embeds: :class:`bool`
        Whether to verify embed permissions as well.
    ctx: Optional[:class:`commands.Context`]
        The context that started this pagination session or ``None`` if it hasn't
        been started yet.
    bot: Optional[:class:`commands.Bot`]
        The bot that is running this pagination session or ``None`` if it hasn't
        been started yet.
    message: Optional[:class:`discord.Message`]
        The message that has been sent for handling the menu. This is the returned
        message of :meth:`send_initial_message`. You can set it in order to avoid
        calling :meth:`send_initial_message`\, if for example you have a pre-existing
        message you want to attach a menu to.
    """

    def __init__(
        self,
        *,
        timeout: float = 180.0,
        delete_message_after: bool = False,
        clear_reactions_after: bool = False,
        check_embeds: bool = False,
        message: Optional[discord.Message] = None
    ) -> None:

        self.timeout = timeout
        self.delete_message_after = delete_message_after
        self.clear_reactions_after = clear_reactions_after
        self.check_embeds = check_embeds
        self._can_remove_reactions = False
        self.__tasks = []
        self._running = True
        self.message = message
        self.ctx: Optional[commands.Context] = None
        self.bot: Optional[commands.Bot] = None
        self._author_id = None
        self._buttons = self.__class__.get_buttons()
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()

    @discord.utils.cached_property
    def buttons(self) -> Dict[discord.PartialEmoji, Button]:
        """Retrieves the buttons that are to be used for this menu session.

        Skipped buttons are not in the resulting dictionary.

        Returns
        ---------
        Mapping[:class:`discord.PartialEmoji`, :class:`Button`]
            A mapping of button emoji to the actual button class.
        """
        buttons = sorted(self._buttons.values(), key=lambda b: b.position)
        return {button.emoji: button for button in buttons if button.is_valid(self)}

    def add_button(self, button: Button, *, react: bool = False) -> _MbyCoro:
        """|maybecoro|

        Adds a button to the list of buttons.

        If the menu has already been started then the button will
        not be added unless the ``react`` keyword-only argument is
        set to ``True``. Note that when this happens this function
        will need to be awaited.

        If a button with the same emoji is added then it is overridden.

        .. warning::

            If the menu has started and the reaction is added, the order
            property of the newly added button is ignored due to an API
            limitation with Discord and the fact that reaction ordering
            is not guaranteed.

        Parameters
        ------------
        button: :class:`Button`
            The button to add.
        react: :class:`bool`
            Whether to add a reaction if the menu has been started.
            Note this turns the method into a coroutine.

        Raises
        ---------
        MenuError
            Tried to use ``react`` when the menu had not been started.
        discord.HTTPException
            Adding the reaction failed.
        """

        self._buttons[button.emoji] = button

        if react:
            if self.__tasks:

                async def wrapped():
                    # Add the reaction
                    try:
                        await self.message.add_reaction(button.emoji)
                    except discord.HTTPException:
                        raise
                    else:
                        # Update the cache to have the value
                        self.buttons[button.emoji] = button

                return wrapped()

            async def dummy():
                raise MenuError('Menu has not been started yet')

            return dummy()

    def remove_button(self, emoji: Union[Button, discord.PartialEmoji, str], *, react: bool = False) -> _MbyCoro:
        """|maybecoro|

        Removes a button from the list of buttons.

        This operates similar to :meth:`add_button`.

        Parameters
        ------------
        emoji: Union[:class:`Button`, :class:`discord.PartialEmoji`, :class:`str`]
            The emoji or the button to remove.
        react: :class:`bool`
            Whether to remove the reaction if the menu has been started.
            Note this turns the method into a coroutine.

        Raises
        ---------
        MenuError
            Tried to use ``react`` when the menu had not been started.
        discord.HTTPException
            Removing the reaction failed.
        """

        if isinstance(emoji, Button):
            emoji = emoji.emoji
        else:
            emoji = _cast_emoji(emoji)

        self._buttons.pop(emoji, None)

        if react:
            if self.__tasks:

                async def wrapped():
                    # Remove the reaction from being processable
                    # Removing it from the cache first makes it so the check
                    # doesn't get triggered.
                    emoji = cast(discord.PartialEmoji, emoji)
                    self.buttons.pop(emoji, None)
                    await self.message.remove_reaction(emoji, self.__me)

                return wrapped()

            async def dummy():
                raise MenuError('Menu has not been started yet')

            return dummy()

    def clear_buttons(self, *, react: bool = False) -> _MbyCoro:
        """|maybecoro|

        Removes all buttons from the list of buttons.

        If the menu has already been started then the buttons will
        not be removed unless the ``react`` keyword-only argument is
        set to ``True``. Note that when this happens this function
        will need to be awaited.

        Parameters
        ------------
        react: :class:`bool`
            Whether to clear the reactions if the menu has been started.
            Note this turns the method into a coroutine.

        Raises
        ---------
        MenuError
            Tried to use ``react`` when the menu had not been started.
        discord.HTTPException
            Clearing the reactions failed.
        """

        self._buttons.clear()

        if react:
            if self.__tasks:

                async def wrapped():
                    # A fast path if we have permissions
                    if self._can_remove_reactions:
                        try:
                            del self.buttons
                        except AttributeError:
                            pass
                        finally:
                            await self.message.clear_reactions()
                        return

                    # Remove the cache (the next call will have the updated buttons)
                    reactions = list(self.buttons.keys())
                    try:
                        del self.buttons
                    except AttributeError:
                        pass

                    for reaction in reactions:
                        await self.message.remove_reaction(reaction, self.__me)

                return wrapped()

            async def dummy():
                raise MenuError('Menu has not been started yet')

            return dummy()

    def should_add_reactions(self) -> bool:
        """:class:`bool`: Whether to add reactions to this menu session."""
        return len(self.buttons) > 0

    def _verify_permissions(self, ctx: commands.Context, channel: discord.abc.Messageable, permissions: discord.Permissions):
        if not permissions.send_messages:
            raise CannotSendMessages()

        if self.check_embeds and not permissions.embed_links:
            raise CannotEmbedLinks()

        self._can_remove_reactions = permissions.manage_messages
        if self.should_add_reactions():
            if not permissions.add_reactions:
                raise CannotAddReactions()
            if not permissions.read_message_history:
                raise CannotReadMessageHistory()

    def reaction_check(self, payload: discord.RawReactionActionEvent) -> bool:
        """The function that is used to check whether the payload should be processed.
        This is passed to :meth:`discord.ext.commands.Bot.wait_for <Bot.wait_for>`.

        There should be no reason to override this function for most users.

        Parameters
        ------------
        payload: :class:`discord.RawReactionActionEvent`
            The payload to check.

        Returns
        ---------
        :class:`bool`
            Whether the payload should be processed.
        """
        if payload.message_id != self.message.id:
            return False
        if payload.user_id not in {self.bot.owner_id, self._author_id, *self.bot.owner_ids}:
            return False

        return payload.emoji in self.buttons

    async def _internal_loop(self) -> Optional[_MbyCoro]:

        self.__timed_out = False
        loop = self.bot.loop
        # Ensure the name exists for the cancellation handling
        tasks = []

        try:
            while self._running:
                tasks = [
                    asyncio.ensure_future(self.bot.wait_for('raw_reaction_add', check=self.reaction_check)),
                    asyncio.ensure_future(self.bot.wait_for('raw_reaction_remove', check=self.reaction_check)),
                ]
                done, pending = await asyncio.wait(tasks, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()

                if len(done) == 0:
                    raise asyncio.TimeoutError()

                # Exception will propagate if e.g. cancelled or timed out
                payload = done.pop().result()
                loop.create_task(self.update(payload))

                # NOTE: Removing the reaction ourselves after it's been done when
                # mixed with the checks above is incredibly racy.
                # There is no guarantee when the MESSAGE_REACTION_REMOVE event will
                # be called, and chances are when it does happen it'll always be
                # after the remove_reaction HTTP call has returned back to the caller
                # which means that the stuff above will catch the reaction that we
                # just removed.

                # For the future sake of myself and to save myself the hours in the future
                # consider this my warning.

        except asyncio.TimeoutError:
            self.__timed_out = True
        finally:
            self._event.set()

            # Cancel any outstanding tasks (if any)
            for task in tasks:
                task.cancel()

            try:
                await self.finalize(self.__timed_out)
            except Exception:
                pass
            finally:
                self.__timed_out = False

            # Can't do any requests if the bot is closed
            if self.bot.is_closed():
                return

            # Wrap it in another block anyway just to ensure
            # nothing leaks out during clean-up
            try:
                if self.delete_message_after:
                    return await self.message.delete()

                if self.clear_reactions_after:
                    if self._can_remove_reactions:
                        return await self.message.clear_reactions()

                    for button_emoji in self.buttons:
                        try:
                            await self.message.remove_reaction(button_emoji, self.__me)
                        except discord.HTTPException:
                            continue
            except Exception:
                pass

    async def update(self, payload: discord.RawReactionActionEvent) -> None:
        """|coro|

        Updates the menu after an event has been received.

        Parameters
        -----------
        payload: :class:`discord.RawReactionActionEvent`
            The reaction event that triggered this update.
        """
        button = self.buttons[payload.emoji]
        if not self._running:
            return

        try:
            if button.lock:
                async with self._lock:
                    if self._running:
                        await button(self, payload)  # type: ignore
            else:
                await button(self, payload)  # type: ignore
        except Exception as exc:
            await self.on_menu_button_error(exc)

    async def on_menu_button_error(self, exc: Exception) -> Any:
        """|coro|

        Handles reporting of errors while updating the menu from events.
        The default behaviour is to log the exception.

        This may be overriden by subclasses.

        Parameters
        ----------
        exc: :class:`Exception`
            The exception which was raised during a menu update.
        """
        # some users may wish to take other actions during or beyond logging
        # which would require awaiting, such as stopping an erroring menu.
        log.exception("Unhandled exception during menu update.", exc_info=exc)

    async def start(
        self, ctx: commands.Context, *, channel: Optional[discord.abc.Messageable] = None, wait: bool = False
    ) -> Any:
        """|coro|

        Starts the interactive menu session.

        Parameters
        -----------
        ctx: :class:`Context`
            The invocation context to use.
        channel: :class:`discord.abc.Messageable`
            The messageable to send the message to. If not given
            then it defaults to the channel in the context.
        wait: :class:`bool`
            Whether to wait until the menu is completed before
            returning back to the caller.

        Raises
        -------
        MenuError
            An error happened when verifying permissions.
        discord.HTTPException
            Adding a reaction failed.
        """

        # Clear the buttons cache and re-compute if possible.
        try:
            del self.buttons
        except AttributeError:
            pass

        self.bot = bot = ctx.bot
        self.ctx = ctx
        self._author_id = ctx.author.id
        channel = cast(discord.abc.Messageable, channel or ctx.channel)
        if isinstance(channel, discord.abc.GuildChannel):  # type: ignore
            me = channel.guild.me
        else:
            me = ctx.bot.user
        permissions = channel.permissions_for(me)  # type: ignore
        self.__me = discord.Object(id=me.id)
        self._verify_permissions(ctx, channel, permissions)
        self._event.clear()
        msg = self.message
        if msg is None:
            self.message = msg = await self.send_initial_message(ctx, channel)

        if self.should_add_reactions():
            # Start the task first so we can listen to reactions before doing anything
            for task in self.__tasks:
                task.cancel()
            self.__tasks.clear()

            self._running = True
            self.__tasks.append(bot.loop.create_task(self._internal_loop()))

            async def add_reactions_task():
                for emoji in self.buttons:
                    await msg.add_reaction(emoji)

            self.__tasks.append(bot.loop.create_task(add_reactions_task()))

            if wait:
                await self._event.wait()

    async def finalize(self, timed_out: bool) -> Any:
        """|coro|

        A coroutine that is called when the menu loop has completed
        its run. This is useful if some asynchronous clean-up is
        required after the fact.

        Parameters
        --------------
        timed_out: :class:`bool`
            Whether the menu completed due to timing out.
        """
        return

    async def send_initial_message(self, ctx: commands.Context, channel: discord.abc.Messageable) -> discord.Message:
        """|coro|

        Sends the initial message for the menu session.

        This is internally assigned to the :attr:`message` attribute.

        Subclasses must implement this if they don't set the
        :attr:`message` attribute themselves before starting the
        menu via :meth:`start`.

        Parameters
        ------------
        ctx: :class:`Context`
            The invocation context to use.
        channel: :class:`discord.abc.Messageable`
            The messageable to send the message to.

        Returns
        --------
        :class:`discord.Message`
            The message that has been sent.
        """
        raise NotImplementedError

    def stop(self) -> None:
        """Stops the internal loop."""
        self._running = False
        for task in self.__tasks:
            task.cancel()
        self.__tasks.clear()


class PageSource(Generic[T]):
    """An interface representing a menu page's data source for the actual menu page.

    Subclasses must implement the backing resource along with the following methods:

    - :meth:`get_page`
    - :meth:`is_paginating`
    - :meth:`format_page`
    """

    async def _prepare_once(self) -> None:
        try:
            # Don't feel like formatting hasattr with
            # the proper mangling
            # read this as follows:
            # if hasattr(self, '__prepare')
            # except that it works as you expect
            self.__prepare
        except AttributeError:
            await self.prepare()
            self.__prepare = True

    async def prepare(self) -> None:
        """|coro|

        A coroutine that is called after initialisation
        but before anything else to do some asynchronous set up
        as well as the one provided in ``__init__``.

        By default this does nothing.

        This coroutine will only be called once.
        """
        return

    def is_paginating(self) -> bool:
        """An abstract method that notifies the :class:`MenuPages` whether or not
        to start paginating. This signals whether to add reactions or not.

        Subclasses must implement this.

        Returns
        --------
        :class:`bool`
            Whether to trigger pagination.
        """
        raise NotImplementedError

    def get_max_pages(self) -> Optional[int]:
        """An optional abstract method that retrieves the maximum number of pages
        this page source has. Useful for UX purposes.

        The default implementation returns ``None``.

        Returns
        --------
        Optional[:class:`int`]
            The maximum number of pages required to properly
            paginate the elements, if given.
        """
        return None

    async def get_page(self, page_number: int) -> T:
        """|coro|

        An abstract method that retrieves an object representing the object to format.

        Subclasses must implement this.

        .. note::

            The page_number is zero-indexed between [0, :meth:`get_max_pages`),
            if there is a maximum number of pages.

        Parameters
        -----------
        page_number: :class:`int`
            The page number to access.

        Returns
        ---------
        Any
            The object represented by that page.
            This is passed into :meth:`format_page`.
        """
        raise NotImplementedError

    async def format_page(self, menu: Menu, page: T) -> Union[str, discord.Embed, Dict[str, Any]]:
        """|maybecoro|

        An abstract method to format the page.

        This method must return one of the following types.

        If this method returns a ``str`` then it is interpreted as returning
        the ``content`` keyword argument in :meth:`discord.Message.edit`
        and :meth:`discord.abc.Messageable.send`.

        If this method returns a :class:`discord.Embed` then it is interpreted
        as returning the ``embed`` keyword argument in :meth:`discord.Message.edit`
        and :meth:`discord.abc.Messageable.send`.

        If this method returns a ``dict`` then it is interpreted as the
        keyword-arguments that are used in both :meth:`discord.Message.edit`
        and :meth:`discord.abc.Messageable.send`. The two of interest are
        ``embed`` and ``content``.

        Parameters
        ------------
        menu: :class:`Menu`
            The menu that wants to format this page.
        page: Any
            The page returned by :meth:`PageSource.get_page`.

        Returns
        ---------
        Union[:class:`str`, :class:`discord.Embed`, :class:`dict`]
            See above.
        """
        raise NotImplementedError


class MenuPages(Menu, Generic[T]):
    """A special type of Menu dedicated to pagination.

    Attributes
    ------------
    current_page: :class:`int`
        The current page that we are in. Zero-indexed
        between [0, :attr:`PageSource.max_pages`).
    """

    def __init__(self, source: PageSource[T], **kwargs: Any):
        self._source = source
        self.current_page: int = 0
        super().__init__(**kwargs)

    @property
    def source(self) -> PageSource:
        """:class:`PageSource`: The source where the data comes from."""
        return self._source

    async def change_source(self, source: PageSource[T]) -> None:
        """|coro|

        Changes the :class:`PageSource` to a different one at runtime.

        Once the change has been set, the menu is moved to the first
        page of the new source if it was started. This effectively
        changes the :attr:`current_page` to 0.

        Raises
        --------
        TypeError
            A :class:`PageSource` was not passed.
        """

        if not isinstance(source, PageSource):
            raise TypeError('Expected {0!r} not {1.__class__!r}.'.format(PageSource, source))

        self._source = source
        self.current_page = 0
        if self.message is not None:
            await source._prepare_once()
            await self.show_page(0)

    def should_add_reactions(self) -> bool:
        return self._source.is_paginating()

    async def _get_kwargs_from_page(self, page) -> Dict[str, Any]:
        value = await discord.utils.maybe_coroutine(self._source.format_page, self, page)
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {'content': value, 'embed': None}
        elif isinstance(value, discord.Embed):
            return {'embed': value, 'content': None}
        return {}  # this should never happen

    async def show_page(self, page_number: int) -> None:
        page = await self._source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        await self.message.edit(**kwargs)

    async def send_initial_message(self, ctx: commands.Context, channel: discord.abc.Messageable) -> None:
        """|coro|

        The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.

        This implementation shows the first page of the source.
        """
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        return await channel.send(**kwargs)

    async def start(self, ctx, *, channel: Optional[discord.abc.Messageable] = None, wait: bool = False) -> None:
        await self._source._prepare_once()
        await super().start(ctx, channel=channel, wait=wait)

    async def show_checked_page(self, page_number: int) -> None:
        max_pages = self._source.get_max_pages()
        try:
            if max_pages is None:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number)
            elif max_pages > page_number >= 0:
                await self.show_page(page_number)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    async def show_current_page(self) -> None:
        if self._source.is_paginating():
            await self.show_page(self.current_page)

    def _skip_double_triangle_buttons(self) -> bool:
        max_pages = self._source.get_max_pages()
        if max_pages is None:
            return True
        return max_pages <= 2

    @button(
        '\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f',
        position=First(0),
        skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_first_page(self, payload: discord.RawReactionActionEvent) -> None:
        """go to the first page"""
        await self.show_page(0)

    @button('\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f', position=First(1))
    async def go_to_previous_page(self, payload: discord.RawReactionActionEvent) -> None:
        """go to the previous page"""
        await self.show_checked_page(self.current_page - 1)

    @button('\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f', position=Last(0))
    async def go_to_next_page(self, payload: discord.RawReactionActionEvent) -> None:
        """go to the next page"""
        await self.show_checked_page(self.current_page + 1)

    @button(
        '\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f',
        position=Last(1),
        skip_if=_skip_double_triangle_buttons,
    )
    async def go_to_last_page(self, payload: discord.RawReactionActionEvent) -> None:
        """go to the last page"""
        # The call here is safe because it's guarded by skip_if
        await self.show_page(self._source.get_max_pages() - 1)

    @button('\N{BLACK SQUARE FOR STOP}\ufe0f', position=Last(2))
    async def stop_pages(self, payload: discord.RawReactionActionEvent) -> None:
        """stops the pagination session."""
        self.stop()


class ListPageSource(PageSource[T], Generic[T]):
    """A data source for a sequence of items.

    This page source does not handle any sort of formatting, leaving it up
    to the user. To do so, implement the :meth:`format_page` method.

    Attributes
    ------------
    entries: Sequence[Any]
        The sequence of items to paginate.
    per_page: :class:`int`
        How many elements are in a page.
    """

    def __init__(self, entries: Sequence[T], *, per_page: int) -> None:
        self.entries = entries
        self.per_page = per_page

        pages, left_over = divmod(len(entries), per_page)
        if left_over:
            pages += 1

        self._max_pages = pages

    def is_paginating(self) -> bool:
        """:class:`bool`: Whether pagination is required."""
        return len(self.entries) > self.per_page

    def get_max_pages(self) -> int:
        """:class:`int`: The maximum number of pages required to paginate this sequence."""
        return self._max_pages

    async def get_page(self, page_number: int) -> Union[T, Sequence[T]]:
        """Returns either a single element of the sequence or
        a slice of the sequence.

        If :attr:`per_page` is set to ``1`` then this returns a single
        element. Otherwise it returns at most :attr:`per_page` elements.

        Returns
        ---------
        Union[Any, List[Any]]
            The data returned.
        """
        if self.per_page == 1:
            return self.entries[page_number]
        base = page_number * self.per_page
        return self.entries[base : base + self.per_page]


class _GroupByEntry(tuple, Generic[KT, IT]):
    if TYPE_CHECKING:
        key: KT
        items: List[IT]

    def __new__(cls, key: KT, items: List[IT]):
        new_cls = super().__new__(cls, (key, items))
        new_cls.key = key
        new_cls.items = items
        return new_cls


class GroupByPageSource(ListPageSource[IT], Generic[KT, IT]):
    """A data source for grouped by sequence of items.

    This inherits from :class:`ListPageSource`.

    This page source does not handle any sort of formatting, leaving it up
    to the user. To do so, implement the :meth:`format_page` method.

    Parameters
    ------------
    entries: Sequence[Any]
        The sequence of items to paginate and group.
    key: Callable[[Any], Any]
        A key function to do the grouping with.
    sort: :class:`bool`
        Whether to sort the sequence before grouping it.
        The elements are sorted according to the ``key`` function passed.
    per_page: :class:`int`
        How many elements to have per page of the group.
    """

    def __init__(self, entries: Sequence[IT], *, key: Callable[[IT], KT], per_page: int, sort: bool = True) -> None:
        self.__entries = entries if not sort else sorted(entries, key=key)
        nested = []
        self.nested_per_page = per_page
        for k, g in itertools.groupby(self.__entries, key=key):
            g = list(g)
            if not g:
                continue
            size = len(g)

            # Chunk the nested pages
            nested.extend(_GroupByEntry(key=k, items=g[i : i + per_page]) for i in range(0, size, per_page))

        super().__init__(nested, per_page=1)

    async def get_page(self, page_number: int) -> IT:
        return self.entries[page_number]

    async def format_page(self, menu: Menu, entry: _GroupByEntry[KT, IT]) -> dict[str, Any]:
        """An abstract method to format the page.

        This works similar to the :meth:`ListPageSource.format_page` except
        the return type of the ``entry`` parameter is documented.

        Parameters
        ------------
        menu: :class:`Menu`
            The menu that wants to format this page.
        entry
            A namedtuple with ``(key, items)`` representing the key of the
            group by function and a sequence of paginated items within that
            group.

        Returns
        ---------
        :class:`dict`
            A dictionary representing keyword-arguments to pass to
            the message related calls.
        """
        raise NotImplementedError


def _aiter(obj: Any, *, _isasync: Callable[[Any], bool] = inspect.iscoroutinefunction) -> AsyncIterator[Any]:
    cls = obj.__class__
    try:
        async_iter = cls.__aiter__
    except AttributeError:
        raise TypeError('{0.__name__!r} object is not an async iterable'.format(cls))

    async_iter = async_iter(obj)
    if _isasync(async_iter):
        raise TypeError('{0.__name__!r} object is not an async iterable'.format(cls))
    return async_iter


class AsyncIteratorPageSource(PageSource[T], Generic[T]):
    """A data source for data backed by an asynchronous iterator.

    This page source does not handle any sort of formatting, leaving it up
    to the user. To do so, implement the :meth:`format_page` method.

    Parameters
    ------------
    iter: AsyncIterator[Any]
        The asynchronous iterator to paginate.
    per_page: :class:`int`
        How many elements to have per page.
    """

    def __init__(self, iterator: AsyncIterator[T], *, per_page: int) -> None:
        self.iterator = _aiter(iterator)
        self.per_page = per_page
        self._exhausted = False
        self._cache = []

    async def _iterate(self, n: int) -> None:
        it = self.iterator
        cache = self._cache
        for _ in range(n):
            try:
                elem = await it.__anext__()
            except StopAsyncIteration:
                self._exhausted = True
                break
            else:
                cache.append(elem)

    async def prepare(self, *, _aiter: Callable[..., AsyncIterator[T]] = _aiter) -> None:
        # Iterate until we have at least a bit more single page
        await self._iterate(self.per_page + 1)

    def is_paginating(self) -> bool:
        """:class:`bool`: Whether pagination is required."""
        return len(self._cache) > self.per_page

    async def _get_single_page(self, page_number: int) -> T:
        if page_number < 0:
            raise IndexError('Negative page number.')

        if not self._exhausted and len(self._cache) <= page_number:
            await self._iterate((page_number + 1) - len(self._cache))
        return self._cache[page_number]

    async def _get_page_range(self, page_number: int) -> Sequence[T]:
        if page_number < 0:
            raise IndexError('Negative page number.')

        base = page_number * self.per_page
        max_base = base + self.per_page
        if not self._exhausted and len(self._cache) <= max_base:
            await self._iterate((max_base + 1) - len(self._cache))

        entries = self._cache[base:max_base]
        if not entries and max_base > len(self._cache):
            raise IndexError('Went too far')
        return entries

    async def get_page(self, page_number) -> Union[T, Sequence[T]]:
        """Returns either a single element of the sequence or
        a slice of the sequence.

        If :attr:`per_page` is set to ``1`` then this returns a single
        element. Otherwise it returns at most :attr:`per_page` elements.

        Returns
        ---------
        Union[Any, List[Any]]
            The data returned.
        """
        if self.per_page == 1:
            return await self._get_single_page(page_number)
        else:
            return await self._get_page_range(page_number)
