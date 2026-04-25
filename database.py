import os
from supabase import create_client, Client

# Supabase Configuration
SUPABASE_URL = "https://iuzlrtkhwkcfvnvvlshm.supabase.co"
SUPABASE_KEY = "sb_publishable_I1IXTGm0Ybv7lq75evTWSw_kG5afuEe" # Using publishable key

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- AUTH & USER MGMT ---

def verify_user(username, password):
    from werkzeug.security import check_password_hash
    res = supabase.table('users').select('*').eq('username', username).eq('is_active', 1).execute()
    user = res.data[0] if res.data else None
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None

def add_user(username, password_hash, role='cashier'):
    data = {
        "username": username,
        "password_hash": password_hash,
        "role": role,
        "is_active": 1
    }
    supabase.table('users').insert(data).execute()

def get_all_cashiers():
    res = supabase.table('users').select('id, username, role').eq('role', 'cashier').eq('is_active', 1).execute()
    return res.data

def delete_user(user_id):
    # Soft delete
    supabase.table('users').update({"is_active": 0}).eq('id', user_id).neq('role', 'owner').execute()

def reset_password(user_id, new_hash):
    supabase.table('users').update({"password_hash": new_hash}).eq('id', user_id).execute()

# --- INVENTORY MGMT ---

def get_all_inventory():
    res = supabase.table('inventory').select('*').eq('is_active', 1).order('item_name').execute()
    return res.data

def add_inventory_item(name, qty, threshold, price, cost=0, category='General', image_url=None, expiry_date=None):
    data = {
        "item_name": name,
        "quantity": qty,
        "min_threshold": threshold,
        "unit_price": price,
        "cost_price": cost,
        "category": category,
        "image_url": image_url,
        "expiry_date": expiry_date,
        "is_active": 1
    }
    supabase.table('inventory').insert(data).execute()

def update_inventory_item(item_id, name=None, qty=None, threshold=None, price=None, cost=None, category=None, image_url=None, expiry_date=None):
    updates = {}
    if name is not None: updates["item_name"] = name
    if qty is not None: updates["quantity"] = qty
    if threshold is not None: updates["min_threshold"] = threshold
    if price is not None: updates["unit_price"] = price
    if cost is not None: updates["cost_price"] = cost
    if category is not None: updates["category"] = category
    if image_url is not None: updates["image_url"] = image_url
    if expiry_date: updates["expiry_date"] = expiry_date
    
    supabase.table('inventory').update(updates).eq('id', item_id).execute()

def update_inventory_quick(item_id, qty_to_add, new_price, cost_price):
    # Get current qty first
    res = supabase.table('inventory').select('quantity').eq('id', item_id).execute()
    current_qty = res.data[0]['quantity'] if res.data else 0
    
    updates = {
        "quantity": current_qty + int(qty_to_add),
        "unit_price": new_price,
        "cost_price": cost_price
    }
    supabase.table('inventory').update(updates).eq('id', item_id).execute()

def delete_inventory_item(item_id):
    # Soft delete
    supabase.table('inventory').update({"is_active": 0}).eq('id', item_id).execute()

# --- SALES & CHECKOUT ---

def add_sale(inventory_id, qty_sold, total_price, cashier_id=None, is_dinau=False, customer_name=None, payment_method='cash', receipt_id=None):
    # Get cost price for profit tracking
    res = supabase.table('inventory').select('cost_price', 'quantity').eq('id', inventory_id).execute()
    item = res.data[0] if res.data else None
    cost_at_sale = (float(item['cost_price']) * int(qty_sold)) if item else 0.0
    
    sale_data = {
        "inventory_id": inventory_id,
        "qty_sold": int(qty_sold),
        "total_price": float(total_price),
        "cost_at_sale": cost_at_sale,
        "payment_method": payment_method,
        "cashier_id": cashier_id,
        "is_dinau": 1 if is_dinau else 0,
        "customer_name": customer_name,
        "receipt_id": receipt_id
    }
    supabase.table('sales').insert(sale_data).execute()
    
    # Update inventory qty
    if item:
        new_qty = item['quantity'] - int(qty_sold)
        supabase.table('inventory').update({"quantity": new_qty}).eq('id', inventory_id).execute()
        clear_inventory_cache()

def get_sales_summary():
    # Fetch all unclosed sales (current session)
    res = supabase.table('sales').select('*').eq('is_closed', 0).execute()
    sales = res.data
    
    total_sales = sum(float(s['total_price']) for s in sales)
    total_cost = sum(float(s['cost_at_sale']) for s in sales)
    dinau_today = sum(float(s['total_price']) for s in sales if s['is_dinau'] == 1)
    
    return {
        'total_sales': total_sales,
        'total_profit': total_sales - total_cost,
        'expected_cash': total_sales - dinau_today
    }

def get_cashier_summary(cashier_id):
    # Fetch all unclosed sales for this cashier
    res = supabase.table('sales').select('total_price').eq('cashier_id', cashier_id).eq('is_closed', 0).execute()
    total = sum(float(s['total_price']) for s in res.data)
    return {'total_sales': total}

# --- ANALYTICS ---

def get_inventory_status():
    res = supabase.table('inventory').select('item_name, quantity, min_threshold').eq('is_active', 1).execute()
    items = res.data
    low_stock = [i for i in items if i['quantity'] <= i['min_threshold']]
    return {
        'total_items': len(items),
        'low_stock': low_stock,
        'needs_restock': len(low_stock)
    }

def get_daily_sales_chart():
    from datetime import datetime, timedelta
    from collections import defaultdict
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    res = supabase.table('sales').select('total_price, sale_date').gte('sale_date', week_ago).execute()
    
    # Group by date
    daily_totals = defaultdict(float)
    for s in res.data:
        # Extract date from timestamp
        d = s['sale_date'].split('T')[0]
        daily_totals[d] += float(s['total_price'])
    
    # Format for chart (list of dicts)
    # Get last 7 days including today
    chart_data = []
    for i in range(6, -1, -1):
        dt = (datetime.now() - timedelta(days=i))
        ds = dt.strftime('%Y-%m-%d')
        chart_data.append({
            'date': dt,
            'total': daily_totals.get(ds, 0.0)
        })
    return chart_data

def get_hourly_sales_today():
    from datetime import date
    from collections import defaultdict
    today = date.today().isoformat()
    res = supabase.table('sales').select('total_price, sale_date').gte('sale_date', today).execute()
    
    hourly = defaultdict(float)
    for s in res.data:
        # Expecting ISO format 2026-04-22T08:00:00...
        hour = s['sale_date'].split('T')[1].split(':')[0]
        hourly[hour] += float(s['total_price'])
    
    data = []
    for h in range(7, 20): # Typical shop hours 7am - 7pm
        hs = f"{h:02d}"
        data.append({'hour': hs, 'total': hourly.get(hs, 0.0)})
    return data

def get_category_sales_distribution():
    from collections import defaultdict
    # This is slightly more complex as categories are in inventory table
    res = supabase.table('sales').select('total_price, inventory(category)').execute()
    
    dist = defaultdict(float)
    for s in res.data:
        cat = s['inventory']['category'] if s.get('inventory') else 'Unknown'
        dist[cat] += float(s['total_price'])
        
    return [{'category': k, 'total': v} for k, v in dist.items()]

def get_detailed_sales_history():
    from dateutil import parser
    res = supabase.table('sales').select('*, inventory(item_name), users(username)').order('sale_date', desc=True).execute()
    # Need to flatten the join
    data = []
    for s in res.data:
        s['item_name'] = s['inventory']['item_name'] if s.get('inventory') else 'Unknown'
        s['cashier'] = s['users']['username'] if s.get('users') else 'Unknown'
        if s.get('sale_date'):
            s['sale_date'] = parser.parse(s['sale_date'])
        data.append(s)
    return data

def cleanup_old_sales():
    """Automatically delete sales records older than 30 days to maintain performance."""
    try:
        from datetime import datetime, timedelta
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        supabase.table('sales').delete().lt('sale_date', thirty_days_ago).execute()
    except Exception as e:
        print(f"Cleanup error: {e}")

def get_sales_history(limit=50):
    from dateutil import parser
    from collections import defaultdict
    
    # Run cleanup periodically (optional, could be moved to a background task)
    # For now, we'll keep it simple
    
    # Fetch sales with joins
    res = supabase.table('sales').select('*, inventory(item_name), users(username)').order('sale_date', desc=True).limit(500).execute()
    
    # Group by receipt_id (or fallback to id for older records)
    receipts_dict = defaultdict(list)
    for s in res.data:
        rid = s.get('receipt_id') or f"REC-{s['id']}"
        s['item_name'] = s['inventory']['item_name'] if s.get('inventory') else 'Unknown'
        s['cashier'] = s['users']['username'] if s.get('users') else 'Unknown'
        if s.get('sale_date'):
            s['sale_date'] = parser.parse(s['sale_date'])
        receipts_dict[rid].append(s)
    
    # Format into receipt objects
    grouped_receipts = []
    for rid, items in receipts_dict.items():
        first = items[0]
        grouped_receipts.append({
            'receipt_id': rid,
            'sale_date': first['sale_date'],
            'cashier': first['cashier'],
            'payment_method': first['payment_method'],
            'customer_name': first['customer_name'],
            'total_price': sum(float(i['total_price']) for i in items),
            'sale_items': items
        })
    
    # Sort by date descending
    grouped_receipts.sort(key=lambda x: x['sale_date'], reverse=True)
    return grouped_receipts[:50]

# --- CATEGORIES ---

from functools import lru_cache

@lru_cache(maxsize=1)
def get_all_categories():
    res = supabase.table('categories').select('*').order('name').execute()
    return res.data

@lru_cache(maxsize=1)
def get_all_inventory():
    res = supabase.table('inventory').select('*').eq('is_active', 1).order('item_name').execute()
    return res.data

def clear_inventory_cache():
    get_all_categories.cache_clear()
    get_all_inventory.cache_clear()

def add_category(name):
    supabase.table('categories').insert({"name": name}).execute()
    clear_inventory_cache()

# --- DINAU ---

def get_all_dinau():
    from dateutil import parser
    res = supabase.table('dinau_records').select('*').order('record_date', desc=True).execute()
    data = []
    for r in res.data:
        if r.get('record_date'):
            r['record_date'] = parser.parse(r['record_date'])
        data.append(r)
    return data

def add_dinau_record(customer_name, amount):
    supabase.table('dinau_records').insert({
        "customer_name": customer_name,
        "amount": amount,
        "status": "unpaid"
    }).execute()

def cleanup_settled_dinau():
    """
    When the number of settled (paid) payments reach 10, delete the oldest 5.
    """
    # Get all settled records ordered by date (oldest first)
    res = supabase.table('dinau_records').select('id').eq('status', 'paid').order('record_date', desc=False).execute()
    paid_records = res.data
    
    if len(paid_records) >= 10:
        # Get IDs of the oldest 5
        ids_to_delete = [r['id'] for r in paid_records[:5]]
        # Delete them
        supabase.table('dinau_records').delete().in_('id', ids_to_delete).execute()

def update_dinau_status(record_id, status):
    supabase.table('dinau_records').update({"status": status}).eq('id', record_id).execute()
    if status == 'paid':
        cleanup_settled_dinau()

# --- REPORTS ---

def close_shop(actual_cash, expected_cash, total_sales=0, total_profit=0, restock_notes=''):
    difference = float(actual_cash) - float(expected_cash)
    from datetime import datetime
    
    report_data = {
        "expected_cash": expected_cash,
        "actual_cash": actual_cash,
        "difference": difference,
        "total_sales": total_sales,
        "total_profit": total_profit,
        "restock_notes": restock_notes,
        "report_date": datetime.now().isoformat(),
        "total_unpaid": total_sales - expected_cash # Approximation
    }
    supabase.table('daily_reports').insert(report_data).execute()
    
    # Mark current sales as closed
    supabase.table('sales').update({"is_closed": 1}).eq('is_closed', 0).execute()
    return difference

def get_all_reports():
    from dateutil import parser
    res = supabase.table('daily_reports').select('*').order('report_date', desc=True).execute()
    data = res.data
    for r in data:
        if r.get('report_date'):
            r['report_date'] = parser.parse(r['report_date'])
    return data

def get_inventory_financials():
    res = supabase.table('inventory').select('quantity, cost_price, unit_price').eq('is_active', 1).execute()
    buying_power = sum(float(i['quantity']) * float(i['cost_price']) for i in res.data)
    shelf_value = sum(float(i['quantity']) * float(i['unit_price']) for i in res.data)
    return {'total_buying_power': buying_power, 'potential_revenue': shelf_value}

def get_expired_items():
    from datetime import datetime, timedelta
    today = datetime.now().date()
    soon = (today + timedelta(days=7)).isoformat()
    
    res = supabase.table('inventory').select('*').eq('is_active', 1).gt('quantity', 0).lte('expiry_date', soon).execute()
    return res.data
