from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/bookbord')
def bookbord():
    return render_template('bookbord.html')

app.run(debug=True)
