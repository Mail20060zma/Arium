import logging
import uuid
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class VectorMemoryManager:
    """
    Управляет долгосрочной векторной памятью ИИ.
    Использует ChromaDB для сохранения эмбеддингов.
    """
    def __init__(self, persist_directory: str = "app/data/chroma"):
        try:
            import chromadb
            from chromadb.config import Settings
            
            self._persist_dir = Path(persist_directory)
            self._persist_dir.parent.mkdir(parents=True, exist_ok=True)
            
            # Поднимаем локального клиента ChromaDB
            self.client = chromadb.PersistentClient(path=str(self._persist_dir))
            
            # Создаем или получаем коллекцию
            # По умолчанию Chroma использует SentenceTransformer (all-MiniLM-L6-v2) 
            self.collection = self.client.get_or_create_collection(
                name="ai_long_term_memory",
                metadata={"hnsw:space": "cosine"}
            )
            self.is_active = True
            logger.info("✅ Векторная память (ChromaDB) успешно инициализирована.")
            
        except ImportError:
            logger.error("❌ Пакет chromadb не установлен. Векторная память отключена.")
            self.is_active = False
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации ChromaDB: {e}")
            self.is_active = False

    def save_memory(self, content: str, category: str = "general") -> str:
        """Сохранить новый факт в память."""
        if not self.is_active:
            return "Ошибка: Векторная память отключена."
            
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        timestamp = time.time()
        
        try:
            self.collection.add(
                documents=[content],
                metadatas=[{"category": category, "timestamp": timestamp}],
                ids=[mem_id]
            )
            logger.info(f"💾 Запомнено [{mem_id}]: {content}")
            return f"Успешно сохранено с ID: {mem_id}"
        except Exception as e:
            logger.error(f"Ошибка сохранения в память: {e}")
            return f"Ошибка сохранения: {e}"

    def search_memory(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        """Искать релевантные факты."""
        if not self.is_active:
            return [{"id": "error", "content": "Векторная память отключена.", "category": "error", "distance": 0.0}]
            
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            found_memories = []
            if results["documents"] and len(results["documents"]) > 0:
                docs = results["documents"][0]
                metas = results["metadatas"][0]
                dists = results["distances"][0] if "distances" in results and results["distances"] else [0.0]*len(docs)
                ids = results["ids"][0]
                
                for i in range(len(docs)):
                    found_memories.append({
                        "id": ids[i],
                        "content": docs[i],
                        "category": metas[i].get("category", "general"),
                        "distance": dists[i]
                    })
            
            return found_memories
        except Exception as e:
            logger.error(f"Ошибка поиска в памяти: {e}")
            return []

    def delete_memory(self, memory_id: str) -> str:
        """Удалить факт по ID."""
        if not self.is_active:
            return "Ошибка: Векторная память отключена."
            
        try:
            self.collection.delete(ids=[memory_id])
            logger.info(f"🗑️ Удалено из памяти: {memory_id}")
            return f"Успешно удалено: {memory_id}"
        except Exception as e:
            logger.error(f"Ошибка удаления из памяти: {e}")
            return f"Ошибка удаления: {e}"

    def update_memory(self, memory_id: str, content: str, category: str = "general") -> str:
        """Обновить существующий факт по ID."""
        if not self.is_active:
            return "Ошибка: Векторная память отключена."
            
        try:
            timestamp = time.time()
            self.collection.update(
                ids=[memory_id],
                documents=[content],
                metadatas=[{"category": category, "timestamp": timestamp}]
            )
            logger.info(f"✏️ Память обновлена [{memory_id}]: {content}")
            return f"Успешно обновлено: {memory_id}"
        except Exception as e:
            logger.error(f"Ошибка обновления памяти: {e}")
            return f"Ошибка обновления: {e}"

    def list_memories(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Получить последние сохраненные факты (без поиска)."""
        if not self.is_active:
            return []
            
        try:
            # Получаем элементы (по умолчанию Chroma возвращает в порядке добавления/хэша)
            results = self.collection.get(limit=limit)
            
            memories = []
            if results["documents"]:
                for i in range(len(results["documents"])):
                    memories.append({
                        "id": results["ids"][i],
                        "content": results["documents"][i],
                        "category": results["metadatas"][i].get("category", "general") if results["metadatas"] else "general"
                    })
            return memories
        except Exception as e:
            logger.error(f"Ошибка при получении списка памяти: {e}")
            return []

# Глобальный экземпляр для переиспользования
vector_memory = VectorMemoryManager()
