"""
Скрипт для подсчета количества markets[] во всех events[] 
в JSON файле polymarket_events_optimized_*.json
"""

import json
import sys
from pathlib import Path
import glob


def count_markets_in_json(json_file_path):
    """
    Считает общее количество markets во всех events в JSON файле
    
    Args:
        json_file_path: путь к JSON файлу
        
    Returns:
        dict с результатами подсчета
    """
    try:
        print(f"Читаю файл: {json_file_path}")
        
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        events = data.get('events', [])
        total_events = len(events)
        total_markets = 0
        events_with_markets = 0
        markets_per_event = []
        
        print(f"Обрабатываю {total_events} events...")
        
        for idx, event in enumerate(events, 1):
            markets = event.get('markets', [])
            num_markets = len(markets)
            total_markets += num_markets
            
            if num_markets > 0:
                events_with_markets += 1
            
            markets_per_event.append({
                'event_id': event.get('id', 'unknown'),
                'event_title': event.get('title', 'unknown'),
                'markets_count': num_markets
            })
            
            # Прогресс каждые 50 events
            if idx % 50 == 0:
                print(f"  Обработано {idx}/{total_events} events...")
        
        # Статистика
        avg_markets = total_markets / total_events if total_events > 0 else 0
        max_markets_event = max(markets_per_event, key=lambda x: x['markets_count']) if markets_per_event else None
        min_markets_event = min(markets_per_event, key=lambda x: x['markets_count']) if markets_per_event else None
        
        results = {
            'file': str(json_file_path),
            'total_events': total_events,
            'total_markets': total_markets,
            'events_with_markets': events_with_markets,
            'events_without_markets': total_events - events_with_markets,
            'average_markets_per_event': round(avg_markets, 2),
            'max_markets_in_event': {
                'count': max_markets_event['markets_count'] if max_markets_event else 0,
                'event_id': max_markets_event['event_id'] if max_markets_event else None,
                'event_title': max_markets_event['event_title'] if max_markets_event else None
            },
            'min_markets_in_event': {
                'count': min_markets_event['markets_count'] if min_markets_event else 0,
                'event_id': min_markets_event['event_id'] if min_markets_event else None,
                'event_title': min_markets_event['event_title'] if min_markets_event else None
            }
        }
        
        return results
    
    except FileNotFoundError:
        print(f"Ошибка: файл {json_file_path} не найден")
        return None
    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON: {e}")
        return None
    except Exception as e:
        print(f"Неожиданная ошибка: {e}")
        return None


def print_results(results):
    """Выводит результаты в удобном формате"""
    if not results:
        return
    
    print("\n" + "="*70)
    print("РЕЗУЛЬТАТЫ ПОДСЧЕТА MARKETS")
    print("="*70)
    print(f"\nФайл: {results['file']}")
    print(f"\nОбщее количество events: {results['total_events']}")
    print(f"Общее количество markets: {results['total_markets']}")
    print(f"\nEvents с markets: {results['events_with_markets']}")
    print(f"Events без markets: {results['events_without_markets']}")
    print(f"Среднее количество markets на event: {results['average_markets_per_event']}")
    
    print(f"\nEvent с максимальным количеством markets:")
    print(f"  Количество: {results['max_markets_in_event']['count']}")
    print(f"  ID: {results['max_markets_in_event']['event_id']}")
    print(f"  Название: {results['max_markets_in_event']['event_title'][:80]}...")
    
    print(f"\nEvent с минимальным количеством markets:")
    print(f"  Количество: {results['min_markets_in_event']['count']}")
    print(f"  ID: {results['min_markets_in_event']['event_id']}")
    print(f"  Название: {results['min_markets_in_event']['event_title'][:80]}...")
    
    print("="*70)


def main():
    """Основная функция"""
    # Если указан путь к файлу как аргумент командной строки
    if len(sys.argv) > 1:
        json_file = sys.argv[1]
    else:
        # Ищем последний файл polymarket_events_optimized_*.json в папке json_output
        json_output_dir = Path('json_output')
        if json_output_dir.exists():
            json_files = list(json_output_dir.glob('polymarket_events_optimized_*.json'))
            if json_files:
                # Берем последний файл (по имени, которое содержит timestamp)
                json_file = max(json_files, key=lambda p: p.name)
                print(f"Автоматически выбран файл: {json_file}")
            else:
                print("Ошибка: не найдено файлов polymarket_events_optimized_*.json в папке json_output")
                return
        else:
            print("Ошибка: папка json_output не найдена")
            return
    
    # Подсчитываем markets
    results = count_markets_in_json(json_file)
    
    # Выводим результаты
    if results:
        print_results(results)
        
        # Сохраняем результаты в JSON
        output_file = Path('output') / 'markets_count_result.json'
        output_file.parent.mkdir(exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nРезультаты также сохранены в: {output_file}")


if __name__ == "__main__":
    main()
