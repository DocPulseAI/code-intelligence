const express = require('express');
const projects = express.Router();
projects.get('/:id', authenticate, authorizeRole, handler);
projects.patch('/:id', authenticate, handler);
module.exports = projects;
