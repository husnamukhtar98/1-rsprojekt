from app import create_app

app = create_app()

if __name__ == '__main__':
    # Starts Flask’s built-in web server in debug mode
    app.run(host='0.0.0.0', port=5000,debug=True)
