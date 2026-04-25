import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from functools import wraps
from werkzeug.utils import secure_filename
from database import (
    verify_user, get_all_inventory, get_all_categories, 
    add_sale, get_sales_history, get_sales_summary, get_inventory_status, 
    get_inventory_financials, get_all_cashiers, add_user,
    reset_password, delete_user, update_inventory_quick, update_inventory_item,
    delete_inventory_item, get_all_dinau, update_dinau_status,
    get_daily_sales_chart, get_hourly_sales_today, get_category_sales_distribution,
    get_expired_items, add_inventory_item, add_category, get_cashier_summary,
    close_shop, get_all_reports, add_dinau_record
)

app = Flask(__name__)
app.secret_key = 'stocksweep_secret_key'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Custom Filter for Currency
@app.template_filter('kina')
def kina_filter(val):
    if val is None: return "K0.00"
    return f"K{float(val):.2f}"

# RBAC Decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def owner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'owner':
            flash("Unauthorized Access: Owners Only", "error")
            return redirect(url_for('pos'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = verify_user(request.form['username'], request.form['password'])
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            if user['role'] == 'cashier':
                return redirect(url_for('pos'))
            return redirect(url_for('dashboard'))
        flash("Invalid Credentials", "error")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- OWNER ONLY ROUTES ---

@app.route('/')
@login_required
def dashboard():
    if session.get('role') == 'owner':
        summary = get_sales_summary()
        profit = summary['total_profit']
    else:
        summary = get_cashier_summary(session.get('user_id'))
        profit = None
        
    expiry_alerts = get_expired_items()
    inventory_status = get_inventory_status()
    chart_data = get_daily_sales_chart()
    hourly_data = get_hourly_sales_today()
    cat_dist = get_category_sales_distribution()
    
    total_alerts = len(expiry_alerts) + inventory_status['needs_restock']
    
    return render_template('dashboard.html', 
                           summary=summary, 
                           profit=profit,
                           expired_items=expiry_alerts,
                           low_stock_count=inventory_status['needs_restock'],
                           total_alerts=total_alerts,
                           chart_data=chart_data,
                           hourly_data=hourly_data,
                           cat_dist=cat_dist)

@app.route('/sales-log')
@login_required
@owner_required
def sales_log():
    logs = get_sales_history()
    return render_template('sales_log.html', history=logs)

@app.route('/reports')
@login_required
@owner_required
def daily_reports_history():
    from database import get_all_reports
    history = get_all_reports()
    return render_template('reports.html', history=history)

# --- SHARED/CASHIER ACCESSIBLE ROUTES ---

@app.route('/pos')
@login_required
def pos():
    inventory = get_all_inventory()
    categories = get_all_categories()
    return render_template('pos.html', inventory=inventory, categories=categories)

@app.route('/inventory')
@login_required
def inventory_mgmt():
    inventory = get_all_inventory()
    categories = get_all_categories()
    financials = get_inventory_financials()
    cashiers = get_all_cashiers() if session.get('role') == 'owner' else []
    return render_template('inventory.html', inventory=inventory, categories=categories, financials=financials, cashiers=cashiers)

@app.route('/dinau')
@login_required
def dinau_mgmt():
    list_items = get_all_dinau()
    return render_template('dinau.html', dinau=list_items)

# --- API & ACTIONS ---

@app.route('/api/checkout', methods=['POST'])
@login_required
def checkout():
    try:
        data = request.json
        items = data.get('items', [])
        payment_method = data.get('payment_method', 'cash')
        customer_name = data.get('customer_name')
        
        if not items:
            return jsonify({'success': False, 'message': 'Cart is empty'}), 400

        total_transaction_amount = sum(float(i['total_price']) for i in items)
        if payment_method == 'dinau' and total_transaction_amount < 20.00:
            return jsonify({'success': False, 'message': 'Minimum K20.00 required for credit sales.'}), 400

        import uuid
        receipt_id = str(uuid.uuid4())[:8].upper()

        for item in items:
            add_sale(
                inventory_id=item['id'],
                qty_sold=item['qty'],
                total_price=item['total_price'],
                cashier_id=session.get('user_id'),
                is_dinau=(payment_method == 'dinau'),
                customer_name=customer_name,
                payment_method=payment_method,
                receipt_id=receipt_id
            )
        
        # Automatically purge sales records older than 30 days
        cleanup_old_sales()

        # If it's a credit (dinau) sale, record the total debt once
        if payment_method == 'dinau' and customer_name:
            add_dinau_record(customer_name, total_transaction_amount)
        
        return jsonify({'success': True})
            
    except Exception as e:
        print(f"Checkout error: {str(e)}")
        return jsonify({'success': False, 'message': f'Server Error: {str(e)}'}), 500

@app.route('/api/dinau/status', methods=['POST'])
@login_required
def update_debt_status():
    record_id = request.form.get('record_id')
    status = request.form.get('status', 'paid')
    if record_id:
        update_dinau_status(record_id, status)
        flash(f"Debt marked as {status.upper()}", "success")
    return redirect(url_for('dinau_mgmt'))

@app.route('/inventory/quick-update', methods=['POST'])
@login_required
def quick_update():
    try:
        item_id = request.form.get('item_id')
        qty_add = int(request.form.get('qty_add', 0))
        new_price = float(request.form.get('new_price'))
        cost_price = float(request.form.get('cost_price'))
        update_inventory_quick(item_id, qty_add, new_price, cost_price)
        flash('Inventory updated successfully!', 'success')
    except Exception as e:
        flash(f'Update failed: {str(e)}', 'error')
    return redirect(url_for('inventory_mgmt'))

@app.route('/inventory/add', methods=['POST'])
@owner_required
def add_product():
    try:
        name = request.form.get('item_name')
        qty = int(request.form.get('quantity'))
        threshold = int(request.form.get('threshold'))
        cost = float(request.form.get('cost'))
        price = float(request.form.get('price'))
        category = request.form.get('category')
        expiry = request.form.get('expiry_date')
        
        image_url = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image_url = url_for('static', filename=f"uploads/{filename}")

        add_inventory_item(name, qty, threshold, price, cost, category, image_url, expiry)
        flash('New product added!', 'success')
    except Exception as e:
        flash(f'Error adding product: {str(e)}', 'error')
    return redirect(url_for('inventory_mgmt'))

@app.route('/category/add', methods=['POST'])
@owner_required
def add_new_category():
    name = request.form.get('category_name')
    if name:
        add_category(name)
        flash('Category added!', 'success')
    return redirect(url_for('inventory_mgmt'))

@app.route('/inventory/update', methods=['POST'])
@owner_required
def update_product():
    try:
        item_id = request.form.get('id')
        item_name = request.form.get('item_name')
        category = request.form.get('category')
        quantity = int(request.form.get('quantity'))
        threshold = int(request.form.get('threshold'))
        cost = float(request.form.get('cost'))
        price = float(request.form.get('price'))
        expiry_date = request.form.get('expiry_date')
        
        image_url = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                filename = secure_filename(f"{item_id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image_url = url_for('static', filename=f"uploads/{filename}")

        update_inventory_item(item_id, item_name, quantity, threshold, price, cost, category, image_url, expiry_date)
        flash('Product updated successfully!', 'success')
    except Exception as e:
        flash(f'Error updating product: {str(e)}', 'error')
    return redirect(url_for('inventory_mgmt'))

@app.route('/inventory/delete/<int:item_id>')
@owner_required
def delete_item(item_id):
    delete_inventory_item(item_id)
    flash("Item deleted successfully", "success")
    return redirect(url_for('inventory_mgmt'))

@app.route('/users/add', methods=['POST'])
@owner_required
def create_user():
    data = request.form
    from werkzeug.security import generate_password_hash
    password_hash = generate_password_hash(data['password'])
    add_user(data['username'], password_hash)
    flash("Cashier registered successfully!", "success")
    return redirect(url_for('inventory_mgmt'))

@app.route('/users/reset', methods=['POST'])
@owner_required
def reset_pw():
    data = request.form
    from werkzeug.security import generate_password_hash
    password_hash = generate_password_hash(data['new_password'])
    reset_password(data['user_id'], password_hash)
    flash("Password reset successfully!", "success")
    return redirect(url_for('inventory_mgmt'))

@app.route('/users/delete', methods=['POST'])
@owner_required
def remove_user():
    user_id = request.form.get('user_id')
    delete_user(user_id)
    flash("User removed successfully.", "success")
    return redirect(url_for('inventory_mgmt'))

@app.route('/api/inventory')
@login_required
def get_inventory_api():
    inventory = get_all_inventory()
    return jsonify(inventory)

@app.route('/reports/close', methods=['POST'])
@owner_required
def close_report():
    actual_cash = float(request.form.get('actual_cash', 0))
    restock_notes = request.form.get('restock_notes', '')
    summary = get_sales_summary()
    close_shop(actual_cash, summary['expected_cash'], summary['total_sales'], summary['total_profit'], restock_notes)
    flash("Shop closed. Daily report generated!", "success")
    return redirect(url_for('dashboard'))

@app.route('/sales-log/purge', methods=['POST'])
@owner_required
def purge_sales():
    try:
        from database import cleanup_old_sales
        cleanup_old_sales()
        flash("Old sales records (30+ days) have been cleared.", "success")
    except Exception as e:
        flash(f"Purge failed: {e}", "error")
    return redirect(url_for('sales_log'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
