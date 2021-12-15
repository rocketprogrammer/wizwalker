import json
from collections import defaultdict
from collections.abc import Iterable
from functools import cached_property, partial
from pathlib import Path
from typing import Optional

from loguru import logger

from wizwalker import utils
from .wad import Wad
from .parsers import parse_template_id_file


class CacheHandler:
    def __init__(self):
        self._wad_cache = None
        self._template_ids = None

    @cached_property
    def _root_wad(self) -> Wad:
        return Wad.from_game_data("Root")

    @cached_property
    def install_location(self) -> Path:
        """
        Wizard101 install location
        """
        return utils.get_wiz_install()

    @cached_property
    def cache_dir(self) -> Path:
        """
        The dir parsed data is stored in
        """
        return utils.get_cache_folder()

    @cached_property
    def langmap_file(self) -> Path:
        return self.cache_dir / "langmap.json"

    @cached_property
    def template_ids_file(self) -> Path:
        return self.cache_dir / "template_ids.json"

    @cached_property
    def wadcache_file(self) -> Path:
        return self.cache_dir / "wad_cache.json"

    async def _check_updated(
        self, wad_file: Wad, files: Iterable[str] | str
    ) -> list[str]:
        if isinstance(files, str):
            files = [files]

        if not self._wad_cache:
            self._wad_cache = await self.get_wad_cache()

        res = []

        for file_name in files:
            file_info = await wad_file.get_info(file_name)

            file_version = dict(crc=file_info.crc, size=file_info.size)
            if self._wad_cache[wad_file.name][file_name].values() != file_version:
                logger.info(
                    f"{file_name} has updated. old: {self._wad_cache[wad_file.name][file_name]} new: {file_version}"
                )
                res.append(file_name)
                self._wad_cache[wad_file.name][file_name] = file_version

            else:
                logger.info(f"{file_name} has not updated from {file_info.size}")

        return res

    async def check_updated(
        self, wad_file: Wad, files: Iterable[str] | str
    ) -> list[str]:
        """
        Checks if some wad files have changed since we last accessed them

        Returns:
            List of the file names that have updated
        """
        res = await self._check_updated(wad_file, files)

        if res:
            await self.write_wad_cache()

        return res

    async def _cache(self):
        """
        Caches various file data
        """
        logger.info("Caching template if needed")
        await self._cache_template(self._root_wad)

    async def _cache_template(self, root_wad):
        template_file = await self.check_updated(root_wad, "TemplateManifest.xml")

        if template_file:
            file_data = await root_wad.read("TemplateManifest.xml")
            parsed_template_ids = parse_template_id_file(file_data)
            del file_data

            json_data = json.dumps(parsed_template_ids)
            await utils.run_in_executor(self.template_ids_file.write_text, json_data)

    async def get_template_ids(self) -> dict:
        """
        Loads template ids from cache

        Returns:
            the loaded template ids
        """
        await self._cache()
        try:
            json_data = await utils.run_in_executor(self.template_ids_file.read_text)
        except FileNotFoundError:
            return {}
        return json.loads(json_data)

    @staticmethod
    def _parse_lang_file(file_data):
        try:
            decoded = file_data.decode("utf-16")

        # empty file
        except UnicodeDecodeError:
            return

        # splitlines splits whitespace that lang files should not recognize as a newline
        file_lines = decoded.split("\r\n")

        header, *lines = file_lines
        _, lang_name = header.split(":")

        lang_mapping = dict(zip(lines[::3], lines[2::3]))

        return {lang_name: lang_mapping}

    @staticmethod
    async def _get_all_lang_file_names(root_wad: Wad) -> list[str]:
        lang_file_names = []

        for file_name in await root_wad.name_list():
            if file_name.startswith("Locale/English/"):
                lang_file_names.append(file_name)

        return lang_file_names

    async def _read_lang_file(self, root_wad: Wad, lang_file: str):
        file_data = await root_wad.read(lang_file)

        if not file_data:
            raise ValueError(f"{lang_file} has not yet been loaded")

        parsed_lang = self._parse_lang_file(file_data)

        return parsed_lang

    async def _cache_lang_file(self, root_wad: Wad, lang_file: str):
        if not await self.check_updated(root_wad, lang_file):
            return

        parsed_lang = await self._read_lang_file(root_wad, lang_file)
        if parsed_lang is None:
            return

        await self._update_langcode_map(parsed_lang)

    async def _cache_lang_files(self, root_wad: Wad):
        lang_file_names = await self._get_all_lang_file_names(root_wad)

        parsed_lang_map = {}
        for file_name in lang_file_names:
            if not await self._check_updated(root_wad, file_name):
                continue
            parsed_lang = await self._read_lang_file(root_wad, file_name)
            if parsed_lang is not None:
                parsed_lang_map.update(parsed_lang)

        await self.write_wad_cache()
        await self._update_langcode_map(parsed_lang_map)

    async def _get_langcode_map(self) -> dict:
        try:
            data = await utils.run_in_executor(self.langmap_file.read_text)
        except FileNotFoundError:
            return {}
        return json.loads(data)

    async def _update_langcode_map(self, langcodes):
        lang_map = await self._get_langcode_map()
        lang_map.update(langcodes)
        await self._write_langcode_map(lang_map)

    async def _write_langcode_map(self, langcode_map):
        json_data = json.dumps(langcode_map)
        await utils.run_in_executor(self.langmap_file.write_text, json_data)

    async def cache_all_langcode_maps(self):
        await self._cache_lang_files(self._root_wad)

    async def get_langcode_map(self) -> dict:
        """
        Gets the langcode map

        {lang_file_name: {code: value}}
        """
        return await self._get_langcode_map()

    async def get_wad_cache(self) -> dict:
        """
        Gets the wad cache data

        Returns:
            a dict with the current cache data
        """
        wad_cache_factory = partial(defaultdict, partial(defaultdict, dict))
        wad_cache = defaultdict(wad_cache_factory)

        try:
            data = await utils.run_in_executor(self.wadcache_file.read_text)
        except FileNotFoundError:
            return wad_cache

        wad_cache_data = json.loads(data)

        # this is so the default dict inside the default dict isn't replaced
        # by .update
        for wad_name, wad_files in wad_cache_data.items():
            for file_name, (crc, size) in wad_files.items():
                wad_cache[wad_name][file_name].update(crc=crc, size=size)

        return wad_cache

    async def write_wad_cache(self):
        """
        Writes wad cache to disk
        """
        json_data = json.dumps(self._wad_cache)
        await utils.run_in_executor(self.wadcache_file.write_text, json_data)

    async def get_template_name(self, template_id: int) -> Optional[str]:
        """
        Get the template name of something by id

        Args:
            template_id: The template id of the item

        Returns:
            str of the template name or None if there is no item with that id
        """
        template_ids = await self.get_template_ids()

        return template_ids.get(str(template_id))

    async def get_langcode_name(self, langcode: str):
        """
        Get the langcode name from the langcode i.e Spells_00001

        Args:
            langcode: Langcode in the format filename_code

        Raises:
            ValueError: If the langcode does not have a match

        """
        try:
            lang_filename, code = langcode.split("_", maxsplit=1)
        except ValueError:
            raise ValueError(f"Invalid langcode format: {langcode}")

        lang_files = await self._get_all_lang_file_names(self._root_wad)

        cached = False
        for filename in lang_files:
            if filename == f"Locale/English/{lang_filename}.lang":
                await self._cache_lang_file(self._root_wad, filename)
                cached = True
                break

        if not cached:
            raise ValueError(f"No lang file named {lang_filename}")

        langcode_map = await self.get_langcode_map()
        lang_file = langcode_map.get(lang_filename)

        if lang_file is None:
            raise ValueError(f"No lang file named {lang_filename}")

        lang_name = lang_file.get(code)

        if lang_name is None:
            raise ValueError(f"No lang name with code {code}")

        return lang_name
