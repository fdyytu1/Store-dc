"""
Product Manager Service
Author: fdyyuk
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-08 14:17:31 UTC
"""

import logging
import asyncio
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime

import discord
from discord.ext import commands

from .constants import (
    Status,
    TransactionError,
    CACHE_TIMEOUT,
    MESSAGES,
    Stock,
    NOTIFICATION_CHANNELS,
    COLORS
)
from database import get_connection
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager

class ProductCallbackManager:
    """Manager untuk mengelola callbacks product service"""
    def __init__(self):
        self.callbacks = {
            'product_created': [],
            'product_updated': [],
            'stock_added': [],
            'stock_updated': [],
            'stock_sold': [],
            'world_updated': [],
            'error': []
        }
    
    def register(self, event_type: str, callback: Callable):
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
    
    async def trigger(self, event_type: str, *args: Any, **kwargs: Any):
        if event_type in self.callbacks:
            for callback in self.callbacks[event_type]:
                try:
                    await callback(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Error in {event_type} callback: {e}")

class ProductResponse:
    """Class untuk standarisasi response dari product service"""
    def __init__(self, success: bool, data: Any = None, message: str = "", error: str = ""):
        self.success = success
        self.data = data
        self.message = message
        self.error = error
        self.timestamp = datetime.utcnow()
    
    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'data': self.data,
            'message': self.message,
            'error': self.error,
            'timestamp': self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    @classmethod
    def success(cls, data: Any = None, message: str = "") -> 'ProductResponse':
        return cls(True, data, message)
    
    @classmethod
    def error(cls, error: str, message: str = "") -> 'ProductResponse':
        return cls(False, None, message, error)

class ProductManagerService(BaseLockHandler):
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
            self.logger = logging.getLogger("ProductManagerService")
            self.cache_manager = CacheManager()
            self.callback_manager = ProductCallbackManager()
            self.setup_default_callbacks()
            self.initialized = True
    
    def setup_default_callbacks(self):
        """Setup default callbacks untuk notifikasi"""
        
        async def notify_product_created(product: Dict):
            """Callback untuk notifikasi produk baru"""
            channel_id = NOTIFICATION_CHANNELS.get('product_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="New Product Created",
                    description=f"Product: {product['name']} ({product['code']})",
                    color=COLORS.SUCCESS
                )
                embed.add_field(name="Price", value=f"{product['price']:,} WL")
                if product['description']:
                    embed.add_field(name="Description", value=product['description'])
                await channel.send(embed=embed)
        
        async def notify_stock_added(product_code: str, quantity: int, added_by: str):
            """Callback untuk notifikasi penambahan stock"""
            channel_id = NOTIFICATION_CHANNELS.get('stock_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Stock Added",
                    description=f"Product: {product_code}",
                    color=COLORS.INFO
                )
                embed.add_field(name="Quantity", value=str(quantity))
                embed.add_field(name="Added By", value=added_by)
                await channel.send(embed=embed)
        
        async def notify_stock_sold(product: Dict, buyer: str, quantity: int):
            """Callback untuk notifikasi penjualan"""
            channel_id = NOTIFICATION_CHANNELS.get('transactions')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Product Sold",
                    description=f"Product: {product['name']} ({product['code']})",
                    color=COLORS.SUCCESS
                )
                embed.add_field(name="Buyer", value=buyer)
                embed.add_field(name="Quantity", value=str(quantity))
                embed.add_field(name="Total Price", value=f"{product['price'] * quantity:,} WL")
                await channel.send(embed=embed)
        
        async def notify_error(operation: str, error: str):
            """Callback untuk notifikasi error"""
            channel_id = NOTIFICATION_CHANNELS.get('error_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Error Occurred",
                    description=f"Operation: {operation}",
                    color=COLORS.ERROR
                )
                embed.add_field(name="Error", value=error)
                await channel.send(embed=embed)
        
        # Register default callbacks
        self.callback_manager.register('product_created', notify_product_created)
        self.callback_manager.register('stock_added', notify_stock_added)
        self.callback_manager.register('stock_sold', notify_stock_sold)
        self.callback_manager.register('error', notify_error)

    async def create_product(self, code: str, name: str, price: int, description: str = None) -> ProductResponse:
        """Create a new product with proper locking and cache invalidation"""
        if price < Stock.MIN_PRICE:
            return ProductResponse.error(MESSAGES.ERROR['INVALID_AMOUNT'])

        lock = await self.acquire_lock(f"product_create_{code}")
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

        conn = None
        try:
            existing = await self.get_product(code)
            if existing:
                return ProductResponse.error(f"Product with code '{code}' already exists")

            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO products (code, name, price, description)
                VALUES (?, ?, ?, ?)
                """,
                (code, name, price, description)
            )
            
            conn.commit()
            
            result = {
                'code': code,
                'name': name,
                'price': price,
                'description': description
            }
            
            # Update cache
            await self.cache_manager.set(
                f"product_{code}", 
                result,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            await self.cache_manager.delete("all_products")
            
            # Trigger callback
            await self.callback_manager.trigger('product_created', result)
            
            return ProductResponse.success(result, MESSAGES.SUCCESS['PRODUCT_CREATED'])

        except Exception as e:
            self.logger.error(f"Error creating product: {e}")
            if conn:
                conn.rollback()
            await self.callback_manager.trigger('error', 'create_product', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"product_create_{code}")

    async def get_product(self, code: str) -> Optional[Dict]:
        """Get product with caching"""
        cache_key = f"product_{code}"
        cached = await self.cache_manager.get(cache_key)
        if cached:
            return cached

        lock = await self.acquire_lock(f"product_get_{code}")
        if not lock:
            self.logger.warning(f"Failed to acquire lock for getting product {code}")
            return None

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM products WHERE code = ? COLLATE NOCASE",
                (code,)
            )
            
            result = cursor.fetchone()
            if result:
                product = dict(result)
                await self.cache_manager.set(
                    cache_key, 
                    product,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
                )
                return product
            return None

        except Exception as e:
            self.logger.error(f"Error getting product: {e}")
            await self.callback_manager.trigger('error', 'get_product', str(e))
            return None
        finally:
            if conn:
                conn.close()
            self.release_lock(f"product_get_{code}")

    async def get_all_products(self) -> ProductResponse:
        """Get all products with caching"""
        cached = await self.cache_manager.get("all_products")
        if cached:
            return ProductResponse.success(cached)

        lock = await self.acquire_lock("products_getall")
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM products ORDER BY code")
            
            products = [dict(row) for row in cursor.fetchall()]
            await self.cache_manager.set(
                "all_products", 
                products,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )
            return ProductResponse.success(products)

        except Exception as e:
            self.logger.error(f"Error getting all products: {e}")
            await self.callback_manager.trigger('error', 'get_all_products', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock("products_getall")

    async def add_stock_item(self, product_code: str, content: str, added_by: str) -> ProductResponse:
        """Add stock item dengan enhanced duplicate checking"""
        # Buat unique lock key berdasarkan content
        content_hash = hashlib.md5(content.encode()).hexdigest()
        lock_key = f"stock_add_{product_code}_{content_hash}"
        
        lock = await self.acquire_lock(lock_key)
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])
    
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Check duplicates dengan index
            cursor.execute("""
                SELECT id FROM stock 
                WHERE product_code = ? AND content = ? 
                AND status != ?
            """, (product_code, content, Status.DELETED.value))
            
            if cursor.fetchone():
                return ProductResponse.error("Duplicate stock content detected")
                
            # Verify product exists
            cursor.execute(
                "SELECT code FROM products WHERE code = ? COLLATE NOCASE",
                (product_code,)
            )
            if not cursor.fetchone():
                return ProductResponse.error(MESSAGES.ERROR['PRODUCT_NOT_FOUND'])
            
            # Check stock limit
            current_stock = await self.get_stock_count(product_code)
            if current_stock >= Stock.MAX_STOCK:
                return ProductResponse.error(f"Stock limit reached ({Stock.MAX_STOCK})")
            
            cursor.execute(
                """
                INSERT INTO stock (product_code, content, added_by, status)
                VALUES (?, ?, ?, ?)
                """,
                (product_code, content, added_by, Status.AVAILABLE.value)
            )
            
            conn.commit()
            
            # Invalidate caches
            await self.cache_manager.delete(f"stock_count_{product_code}")
            await self.cache_manager.delete(f"stock_{product_code}")
            
            # Trigger callback
            await self.callback_manager.trigger('stock_added', product_code, 1, added_by)
            
            return ProductResponse.success(None, MESSAGES.SUCCESS['STOCK_ADDED'])

        except Exception as e:
            self.logger.error(f"Error adding stock item: {e}")
            if conn:
                conn.rollback()
            await self.callback_manager.trigger('error', 'add_stock_item', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"stock_add_{product_code}")

    async def get_available_stock(self, product_code: str, quantity: int = 1) -> ProductResponse:
        """Get available stock with proper locking"""
        if quantity < 1:
            return ProductResponse.error(MESSAGES.ERROR['INVALID_AMOUNT'])
            
        cache_key = f"stock_{product_code}_q{quantity}"
        cached = await self.cache_manager.get(cache_key)
        if cached:
            return ProductResponse.success(cached)

        lock = await self.acquire_lock(f"stock_get_{product_code}")
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, content, added_at
                FROM stock
                WHERE product_code = ? AND status = ?
                ORDER BY added_at ASC
                LIMIT ?
            """, (product_code, Status.AVAILABLE.value, quantity))
            
            result = [{
                'id': row['id'],
                'content': row['content'],
                'added_at': row['added_at']
            } for row in cursor.fetchall()]

            await self.cache_manager.set(
                cache_key, 
                result,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )
            return ProductResponse.success(result)

        except Exception as e:
            self.logger.error(f"Error getting available stock: {e}")
            await self.callback_manager.trigger('error', 'get_available_stock', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"stock_get_{product_code}")

    async def get_stock_count(self, product_code: str) -> ProductResponse:
        """Get stock count with caching"""
        cache_key = f"stock_count_{product_code}"
        cached = await self.cache_manager.get(cache_key)
        if cached is not None:
            return ProductResponse.success(cached)

        lock = await self.acquire_lock(f"stock_count_{product_code}")
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM stock 
                WHERE product_code = ? AND status = ?
            """, (product_code, Status.AVAILABLE.value))
            
            result = cursor.fetchone()['count']
            await self.cache_manager.set(
                cache_key, 
                result,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )
            return ProductResponse.success(result)

        except Exception as e:
            self.logger.error(f"Error getting stock count: {e}")
            await self.callback_manager.trigger('error', 'get_stock_count', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"stock_count_{product_code}")

    async def update_stock_status(
        self, 
        stock_id: int, 
        status: str, 
        buyer_id: str = None
    ) -> ProductResponse:
        """Update stock status with proper locking"""
        if status not in [s.value for s in Status]:
            return ProductResponse.error(f"Invalid status: {status}")
            
        lock = await self.acquire_lock(f"stock_update_{stock_id}")
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get product code first for cache invalidation
            cursor.execute(
                "SELECT product_code FROM stock WHERE id = ?", 
                (stock_id,)
            )
            product_result = cursor.fetchone()
            if not product_result:
                return ProductResponse.error(MESSAGES.ERROR['STOCK_NOT_FOUND'])
            
            product_code = product_result['product_code']
            
            update_query = """
                UPDATE stock 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
            """
            params = [status]

            if buyer_id:
                update_query += ", buyer_id = ?"
                params.append(buyer_id)

            update_query += " WHERE id = ?"
            params.append(stock_id)

            cursor.execute(update_query, params)
            conn.commit()
            
            # Invalidate relevant caches
            await self.cache_manager.delete(f"stock_count_{product_code}")
            await self.cache_manager.delete(f"stock_{product_code}")
            for i in range(1, Stock.MAX_ITEMS + 1):
                await self.cache_manager.delete(f"stock_{product_code}_q{i}")
            
            # Get product info for callback
            product = await self.get_product(product_code)
            
            # Trigger callbacks
            await self.callback_manager.trigger('stock_updated', stock_id, status)
            if status == Status.SOLD.value and buyer_id:
                await self.callback_manager.trigger('stock_sold', product, buyer_id, 1)
            
            return ProductResponse.success(None, "Stock status updated successfully")

        except Exception as e:
            self.logger.error(f"Error updating stock status: {e}")
            if conn:
                conn.rollback()
            await self.callback_manager.trigger('error', 'update_stock_status', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"stock_update_{stock_id}")

    async def get_world_info(self) -> ProductResponse:
        """Get world info with caching"""
        cached = await self.cache_manager.get("world_info")
        if cached:
            return ProductResponse.success(cached)

        lock = await self.acquire_lock("world_info_get")
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM world_info WHERE id = 1")
            result = cursor.fetchone()
            
            if result:
                info = dict(result)
                await self.cache_manager.set(
                    "world_info", 
                    info,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                )
                return ProductResponse.success(info)
            return ProductResponse.error("World info not found")

        except Exception as e:
            self.logger.error(f"Error getting world info: {e}")
            await self.callback_manager.trigger('error', 'get_world_info', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock("world_info_get")

    async def update_world_info(self, world: str, owner: str, bot: str) -> ProductResponse:
        """Update world info with proper locking"""
        lock = await self.acquire_lock("world_info_update")
        if not lock:
            return ProductResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE world_info 
                SET world = ?, owner = ?, bot = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
            """, (world, owner, bot))
            
            conn.commit()
            
            # Invalidate cache
            await self.cache_manager.delete("world_info")
            
            result = {
                'world': world,
                'owner': owner,
                'bot': bot
            }
            
            # Trigger callback
            await self.callback_manager.trigger('world_updated', result)
            
            return ProductResponse.success(result, "World info updated successfully")

        except Exception as e:
            self.logger.error(f"Error updating world info: {e}")
            if conn:
                conn.rollback()
            await self.callback_manager.trigger('error', 'update_world_info', str(e))
            return ProductResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock("world_info_update")

    async def cleanup(self):
        """Cleanup resources before unloading"""
        try:
            patterns = [
                "product_*",
                "stock_*",
                "world_info",
                "all_products"
            ]
            for pattern in patterns:
                await self.cache_manager.delete_pattern(pattern)
            self.logger.info("ProductManagerService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during ProductManagerService cleanup: {e}")
    async def verify_dependencies(self) -> bool:
        """Verify all required dependencies are available"""
        try:
            # Verifikasi koneksi database
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT 1")  # Simple test query
                cursor.fetchone()
                return True
            finally:
                if conn:
                    conn.close()
        except Exception as e:
            self.logger.error(f"Failed to verify dependencies: {e}")
            return False

class ProductManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.product_service = ProductManagerService(bot)
        self.logger = logging.getLogger("ProductManagerCog")

    async def cog_load(self):
        self.logger.info("ProductManagerCog loading...")
        
    async def cog_unload(self):
        await self.product_service.cleanup()
        self.logger.info("ProductManagerCog unloaded")

async def setup(bot):
    if not hasattr(bot, 'product_manager_loaded'):
        cog = ProductManagerCog(bot)
        
        # Verify dependencies
        if not await cog.product_service.verify_dependencies():
            raise Exception("ProductManager dependencies verification failed")
            
        await bot.add_cog(cog)
        bot.product_manager_loaded = True
        logging.info(
            f'ProductManager cog loaded successfully at '
            f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
        )