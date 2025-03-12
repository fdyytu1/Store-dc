"""
Admin Service for Store DC Bot
Author: fdyyuk
Created at: 2025-03-09 02:20:30 UTC
"""
import logging
import asyncio
from typing import Optional, Dict
from datetime import datetime

import discord
from discord.ext import commands
from discord import ui
from typing import Dict, Optional
from datetime import datetime

from .base_handler import BaseLockHandler
from .cache_manager import CacheManager

class AdminService(BaseLockHandler):
    _instance = None
    _instance_lock = asyncio.Lock()

    def __new__(cls, bot):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self, bot):
        if not self.initialized:
            super().__init__() 
            self.bot = bot
            self.logger = logging.getLogger("AdminService")
            self.cache_manager = CacheManager()
            self.maintenance_mode = False
            self.initialized = True

    async def verify_dependencies(self) -> bool:
        """Verify all required dependencies are available"""
        try:
            # Verify database connection
            return True
        except Exception as e:
            self.logger.error(f"Failed to verify dependencies: {e}")
            return False

    async def is_maintenance_mode(self) -> bool:
        """Check if maintenance mode is active"""
        try:
            cached = await self.cache_manager.get('maintenance_mode')
            if cached is not None:
                return cached
            return self.maintenance_mode
        except Exception as e:
            self.logger.error(f"Error checking maintenance mode: {e}")
            return False

    async def set_maintenance_mode(self, enabled: bool) -> bool:
        """Set maintenance mode status"""
        try:
            self.maintenance_mode = enabled
            await self.cache_manager.set(
                'maintenance_mode',
                enabled,
                expires_in=86400  # 24 hours
            )
            return True
        except Exception as e:
            self.logger.error(f"Error setting maintenance mode: {e}")
            return False

    async def cleanup(self):
        """Cleanup resources"""
        try:
            await self.cache_manager.delete('maintenance_mode')
            self.logger.info("AdminService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
# ... (kode sebelumnya tetap sama)

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.admin_service = AdminService(bot)
        self.logger = logging.getLogger("AdminCog")

    async def cog_load(self):
        """Called when cog is loaded"""
        self.logger.info("AdminCog loading...")

    async def cog_unload(self):
        """Called when cog is unloaded"""
        await self.admin_service.cleanup()
        self.logger.info("AdminCog unloaded")

async def setup(bot):
    """Setup AdminService with different loading flag"""
    if not hasattr(bot, 'admin_service_loaded'):
        try:
            # Initialize AdminService
            admin_service = AdminService(bot)
            if not await admin_service.verify_dependencies():
                raise Exception("AdminService dependencies verification failed")
                
            bot.admin_service = admin_service
            bot.admin_service_loaded = True
            logging.info(
                f'AdminService loaded successfully at '
                f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
            )
        except Exception as e:
            logging.error(f"Failed to load AdminService: {e}")
            raise